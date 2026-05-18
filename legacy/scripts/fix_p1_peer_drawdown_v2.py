"""
P1-#8 v3 (peer_drawdown 升级): 用 max_drawdown_m6 替代 -return_d30 作为同侪跌幅代理.

理由:
    v2 用 -return_d30 平均, 但港股近年 IPO d30 多正收益, clamp 后 p50=0,
    区分度差. max_drawdown_m6 是 6 个月内的最大跌幅 (≥0), 区分度更好.

防 look-ahead:
    peer 仅取 listing_date < pricing_date - 6 个月 (m6 数据已知后才可用).

用法:
    python scripts/fix_p1_peer_drawdown_v2.py --dry-run
    python scripts/fix_p1_peer_drawdown_v2.py
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # peer 池: max_drawdown_m6 + listing_date + chapter
    peer_rows = cur.execute("""
        SELECT m.listing_chapter, m.listing_date, r.max_drawdown_m6
        FROM ipo_master m
        JOIN ipo_returns r ON r.ipo_id = m.ipo_id
        WHERE m.listing_date IS NOT NULL AND r.max_drawdown_m6 IS NOT NULL
    """).fetchall()
    by_chap = defaultdict(list)
    for ch, ld, dd in peer_rows:
        by_chap[ch].append((str(ld)[:10], float(dd)))
    for ch in by_chap:
        by_chap[ch].sort()
    print(f"[pool] peer 池: { {k: len(v) for k, v in by_chap.items()} }")

    rows = cur.execute("""
        SELECT ipo_id, pricing_date, listing_chapter
        FROM ipo_master
    """).fetchall()
    print(f"[scope] 待重算 ipo_master: {len(rows)}")

    updates = []
    samples = []
    n_hit = 0
    n_global_fallback = 0
    for ipo_id, pd_str, ch in rows:
        if not pd_str:
            updates.append((None, ipo_id))
            continue
        pd_iso = str(pd_str)[:10]
        try:
            pd_d = date.fromisoformat(pd_iso)
        except Exception:
            updates.append((None, ipo_id))
            continue
        # peer.listing_date < pricing_date - 180d (m6 已知)
        cutoff = (pd_d - timedelta(days=180)).isoformat()
        pool = by_chap.get(ch, [])
        peers = [dd for ld, dd in pool if ld < cutoff]
        if len(peers) < 5:
            all_peers = [dd for k, lst in by_chap.items() for ld, dd in lst if ld < cutoff]
            if len(all_peers) < 5:
                updates.append((None, ipo_id))
                continue
            peers = all_peers
            n_global_fallback += 1
        # max_drawdown_m6 已 ≥ 0; 直接均值
        dd_avg = sum(peers) / len(peers)
        # 截断到 [0, 0.6]
        dd_avg = max(0.0, min(0.6, dd_avg))
        updates.append((dd_avg, ipo_id))
        samples.append(dd_avg)
        n_hit += 1

    print(f"[hit] {n_hit}/{len(rows)} (global_fallback={n_global_fallback})")
    if samples:
        s = sorted(samples)
        n = len(s)
        print(f"  peer_drawdown_v3: n={n} min={s[0]:.3f} p10={s[n//10]:.3f} "
              f"p50={s[n//2]:.3f} p90={s[(n*9)//10]:.3f} max={s[-1]:.3f}")
        print(f"  unique: {len(set(round(x, 4) for x in samples))}")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        conn.close()
        return 0

    cur.executemany(
        "UPDATE ipo_master SET peer_lockup_avg_drawdown=? WHERE ipo_id=?", updates
    )
    conn.commit()

    print("\n--- 验证 ---")
    n_pd = cur.execute(
        "SELECT COUNT(*) FROM ipo_master WHERE peer_lockup_avg_drawdown IS NOT NULL"
    ).fetchone()[0]
    print(f"  peer_lockup_avg_drawdown 非 NULL: {n_pd}/{len(rows)}")
    print(f"  unique: "
          f"{cur.execute('SELECT COUNT(DISTINCT peer_lockup_avg_drawdown) FROM ipo_master').fetchone()[0]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
