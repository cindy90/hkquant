"""
P1-#8 (扩展) 修复: 用本地 csv 间接补 overhang_ratio / peer_lockup_avg_drawdown.

数据源:
    1. data/raw/ifind/ifind_share_capital.csv  (post/pre/actual_issued_shares)
    2. ipo_returns.return_d30 (来自 data/derived/ipo_d30_returns.csv)

间接公式 (用户指定方式):
    - overhang_ratio = pre_ipo_shares / post_ipo_shares
        老股占比越高 -> 解禁时供给压力越大 (取值范围约 0.7~0.95)
    - peer_lockup_avg_drawdown = mean( -return_d30 )
        同 listing_chapter + listing_date < pricing_date 的同侪
        return_d30 是 d30/d1_open - 1 (上市后 30 天回报),
        取负 -> 跌幅;再求均值作为同侪解禁期跌幅代理.

防 look-ahead:
    - peer 仅取 pricing_date 之前已上市的 IPO
    - share_capital 是上市文件披露,pricing_date 时已知,无 look-ahead

用法:
    python scripts/fix_p1_lockup_context_v2.py --dry-run
    python scripts/fix_p1_lockup_context_v2.py
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"
SHARE_CSV = ROOT / "data" / "raw" / "ifind" / "ifind_share_capital.csv"

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _norm(code: str) -> str:
    if not isinstance(code, str):
        return code
    h, _, t = code.partition(".")
    h = h.lstrip("0") or "0"
    return h + ("." + t if t else "")


def load_share_capital() -> dict:
    """读 csv -> {归一化 stock_code: (pre, post, issued)}."""
    out = {}
    with open(SHARE_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sc = _norm((row.get("thscode") or "").strip())
            if not sc:
                continue
            try:
                pre = float(row.get("pre_ipo_shares") or 0) or None
                post = float(row.get("post_ipo_shares") or 0) or None
                issued = float(row.get("actual_issued_shares") or 0) or None
            except ValueError:
                continue
            if pre and post and post > 0:
                out[sc] = (pre, post, issued)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2
    if not SHARE_CSV.exists():
        print(f"CSV 不存在: {SHARE_CSV}")
        return 2

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 1) ALTER TABLE 加 4 列 (idempotent)
    existing = {r[1] for r in cur.execute("PRAGMA table_info(ipo_master)").fetchall()}
    new_cols = []
    for c, t in [
        ("pre_ipo_shares", "REAL"),
        ("post_ipo_shares", "REAL"),
        ("overhang_ratio", "REAL"),
        ("peer_lockup_avg_drawdown", "REAL"),
    ]:
        if c not in existing:
            new_cols.append((c, t))

    if new_cols and not args.dry_run:
        for c, t in new_cols:
            cur.execute(f"ALTER TABLE ipo_master ADD COLUMN {c} {t}")
        conn.commit()
        print(f"[step1] 新加列: {[c for c, _ in new_cols]}")
    else:
        print(f"[step1] 列已存在或 dry-run, new_cols={[c for c, _ in new_cols]}")

    # 2) 加载 share_capital csv
    share_map = load_share_capital()
    print(f"[step2] share_capital.csv 加载: {len(share_map)} 条")

    rows = cur.execute("""
        SELECT ipo_id, stock_code, pricing_date, listing_date, listing_chapter
        FROM ipo_master
    """).fetchall()
    print(f"[step3] ipo_master 共 {len(rows)} 行")

    # 2a) overhang_ratio: 直接 csv lookup
    sh_updates = []
    sh_hits = 0
    overhang_samples = []
    for ipo_id, sc, _pd, _ld, _ch in rows:
        sc_norm = _norm(sc or "")
        v = share_map.get(sc_norm)
        if v:
            pre, post, _issued = v
            ratio = pre / post if post > 0 else None
            sh_updates.append((pre, post, ratio, ipo_id))
            sh_hits += 1
            if ratio is not None:
                overhang_samples.append(ratio)
        else:
            sh_updates.append((None, None, None, ipo_id))

    print(f"  share_capital hit: {sh_hits}/{len(rows)} ({sh_hits/len(rows)*100:.1f}%)")
    if overhang_samples:
        s = sorted(overhang_samples)
        n = len(s)
        print(f"  overhang_ratio: n={n} min={s[0]:.3f} p10={s[n//10]:.3f} "
              f"p50={s[n//2]:.3f} p90={s[(n*9)//10]:.3f} max={s[-1]:.3f}")

    # 2b) peer_lockup_avg_drawdown: 同 chapter + listing_date < pricing_date 的 -return_d30 均值
    # 取数据
    peer_rows = cur.execute("""
        SELECT m.ipo_id, m.listing_chapter, m.listing_date, r.return_d30
        FROM ipo_master m
        LEFT JOIN ipo_returns r ON r.ipo_id = m.ipo_id
        WHERE m.listing_date IS NOT NULL AND r.return_d30 IS NOT NULL
    """).fetchall()
    # 按 chapter 分桶 [(listing_date_iso, return_d30)]
    by_chap = defaultdict(list)
    for ipo_id, ch, ld, r30 in peer_rows:
        by_chap[ch].append((str(ld)[:10], float(r30)))
    for ch in by_chap:
        by_chap[ch].sort()  # asc by listing_date
    print(f"  peer pool: { {k: len(v) for k, v in by_chap.items()} }")

    pd_updates = []
    pd_hits = 0
    pd_samples = []
    for ipo_id, _sc, pd_str, _ld, ch in rows:
        if not pd_str or not ch:
            pd_updates.append((None, ipo_id))
            continue
        pd_iso = str(pd_str)[:10]
        pool = by_chap.get(ch, [])
        # 取 listing_date < pricing_date 的 return_d30
        peers = [r for ld, r in pool if ld < pd_iso]
        if len(peers) < 5:
            # 同 chapter 太少, 用全局兜底
            all_peers = [r for k, lst in by_chap.items() for ld, r in lst if ld < pd_iso]
            if len(all_peers) < 5:
                pd_updates.append((None, ipo_id))
                continue
            peers = all_peers
        # 跌幅 = -return_d30 (return 为负 -> 跌幅为正)
        drawdown = -sum(peers) / len(peers)
        # 截断到 [0, 0.5] (避免极端值)
        drawdown = max(0.0, min(0.5, drawdown))
        pd_updates.append((drawdown, ipo_id))
        pd_hits += 1
        pd_samples.append(drawdown)

    print(f"  peer_drawdown hit: {pd_hits}/{len(rows)} ({pd_hits/len(rows)*100:.1f}%)")
    if pd_samples:
        s = sorted(pd_samples)
        n = len(s)
        print(f"  peer_drawdown: n={n} min={s[0]:.3f} p10={s[n//10]:.3f} "
              f"p50={s[n//2]:.3f} p90={s[(n*9)//10]:.3f} max={s[-1]:.3f}")
        print(f"  unique: {len(set(round(x, 4) for x in pd_samples))}")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        conn.close()
        return 0

    # 写库
    cur.executemany("""
        UPDATE ipo_master
        SET pre_ipo_shares=?, post_ipo_shares=?, overhang_ratio=?
        WHERE ipo_id=?
    """, sh_updates)
    cur.executemany("""
        UPDATE ipo_master SET peer_lockup_avg_drawdown=? WHERE ipo_id=?
    """, pd_updates)
    conn.commit()

    # 3) 验证
    print("\n--- 验证 ---")
    n_oh = cur.execute("SELECT COUNT(*) FROM ipo_master WHERE overhang_ratio IS NOT NULL").fetchone()[0]
    n_pd = cur.execute("SELECT COUNT(*) FROM ipo_master WHERE peer_lockup_avg_drawdown IS NOT NULL").fetchone()[0]
    n_pre = cur.execute("SELECT COUNT(*) FROM ipo_master WHERE pre_ipo_shares IS NOT NULL").fetchone()[0]
    print(f"  pre_ipo_shares 非 NULL:           {n_pre}/{len(rows)}")
    print(f"  overhang_ratio 非 NULL:            {n_oh}/{len(rows)}")
    print(f"  peer_lockup_avg_drawdown 非 NULL: {n_pd}/{len(rows)}")
    print(f"  overhang unique: "
          f"{cur.execute('SELECT COUNT(DISTINCT overhang_ratio) FROM ipo_master').fetchone()[0]}")
    print(f"  peer_drawdown unique: "
          f"{cur.execute('SELECT COUNT(DISTINCT peer_lockup_avg_drawdown) FROM ipo_master').fetchone()[0]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
