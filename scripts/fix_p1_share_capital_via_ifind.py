"""
P1-#8 v3 修复: 通过 iFinD THS_BD 拉取股本 (替代读本地 csv).

替换原因 (用户要求):
    fix_p1_lockup_context_v2.py 直接读 data/raw/ifind/ifind_share_capital.csv,
    本地 csv 是静态快照, ipo_master 新增 IPO 时无法实时更新.
    本脚本走 iFinD API, 后续可纳入定时任务自动同步.

iFinD 接口 (与 src/data_sources/ifind/full_data_pull.py:295-326 同):
    THS_BD(stock_codes, 'ths_total_shares_after_ipo_ld_global;currency_unit', ';')
        ths_total_shares_after_ipo_ld_global -> post_ipo_shares (首发后总股本)
        currency_unit                        -> actual_issued_shares (含超额配售)
    pre_ipo_shares = post - actual_issued
    overhang_ratio = pre / post

同步刷新本地 csv (data/raw/ifind/ifind_share_capital.csv) 作为冷备.

用法:
    python scripts/fix_p1_share_capital_via_ifind.py --dry-run
    python scripts/fix_p1_share_capital_via_ifind.py
    python scripts/fix_p1_share_capital_via_ifind.py --skip-csv  # 不刷 csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"
CSV_OUT = ROOT / "data" / "raw" / "ifind" / "ifind_share_capital.csv"

sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BATCH_SIZE = 50  # 单批 stock_code 数 (THS_BD 限制)
RELOGIN_EVERY_N_BATCH = 8  # 每 N 批重 login 一次 (防 ec=-1010)


def _norm(code: str) -> str:
    if not isinstance(code, str):
        return code
    h, _, t = code.partition(".")
    h = h.lstrip("0") or "0"
    return h + ("." + t if t else "")


def relogin():
    """强制重新登录 (清缓存)."""
    from src.data_sources.ifind import market_env_fetcher as mef
    mef._LOGIN_OK = False
    mef.login_ifind()


def fetch_share_capital_batch(codes: list[str]) -> list[dict]:
    """单批调用 THS_BD, 返回 [{thscode, post_ipo_shares, actual_issued_shares, pre_ipo_shares}]."""
    from iFinDPy import THS_BD
    codes_str = ",".join(codes)
    indicators = "ths_total_shares_after_ipo_ld_global;currency_unit"
    r = THS_BD(codes_str, indicators, ";")
    if r.errorcode != 0 or r.data is None:
        raise RuntimeError(f"THS_BD ec={r.errorcode} msg={r.errmsg}")
    df = r.data
    out = []
    for _, row in df.iterrows():
        sc = str(row.get("thscode", "")).strip()
        if not sc:
            continue
        try:
            post = float(row.get("ths_total_shares_after_ipo_ld_global") or 0) or None
            issued = float(row.get("currency_unit") or 0) or None
        except (TypeError, ValueError):
            post = issued = None
        pre = (post - issued) if (post and issued and post > issued) else None
        out.append({
            "thscode": sc,
            "post_ipo_shares": post,
            "actual_issued_shares": issued,
            "pre_ipo_shares": pre,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-csv", action="store_true", help="不刷新本地 csv 冷备")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    # 1) 登录 iFinD
    relogin()
    print("[step1] iFinD 登录 OK")

    # 2) 取 ipo_master 全量 stock_code
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute("SELECT ipo_id, stock_code FROM ipo_master").fetchall()
    print(f"[step2] ipo_master 共 {len(rows)} 行, 准备拉 share_capital")

    # 用归一化 sc 去重 (因 ipo_master 同公司可能 03296.HK / 3296.HK 但已 P1-#9 dedupe)
    all_codes = sorted({r[1] for r in rows if r[1]})
    print(f"  unique stock_code: {len(all_codes)}")

    # 3) 分批拉取
    results = {}  # _norm(sc) -> dict
    t0 = time.time()
    for bi, i in enumerate(range(0, len(all_codes), BATCH_SIZE)):
        if bi > 0 and bi % RELOGIN_EVERY_N_BATCH == 0:
            relogin()
        batch = all_codes[i: i + BATCH_SIZE]
        try:
            recs = fetch_share_capital_batch(batch)
        except Exception as e:
            print(f"  ⚠ batch {bi} ({i}..{i+len(batch)}) 失败: {e}; 重登重试")
            time.sleep(2)
            try:
                relogin()
                recs = fetch_share_capital_batch(batch)
            except Exception as e2:
                print(f"  ✗ batch {bi} 重试仍失败: {e2}, 跳过")
                continue
        for rec in recs:
            results[_norm(rec["thscode"])] = rec
        time.sleep(0.5)
        sys.stdout.write(f"\r  batch {bi+1}/{(len(all_codes)+BATCH_SIZE-1)//BATCH_SIZE} 已拉 {len(results)}    ")
        sys.stdout.flush()
    print(f"\n  iFinD 拉取完成: {len(results)} 条, 耗时 {time.time()-t0:.1f}s")

    # 4) 统计可计算 overhang 的覆盖
    n_with_pre = sum(1 for v in results.values() if v.get("pre_ipo_shares"))
    print(f"  pre_ipo_shares 推算成功: {n_with_pre}/{len(results)}")

    # 5) ALTER TABLE (idempotent) — 与 v2 同列
    existing = {r[1] for r in cur.execute("PRAGMA table_info(ipo_master)").fetchall()}
    needed = [
        ("pre_ipo_shares", "REAL"),
        ("post_ipo_shares", "REAL"),
        ("overhang_ratio", "REAL"),
    ]
    new_cols = [(c, t) for c, t in needed if c not in existing]
    if new_cols and not args.dry_run:
        for c, t in new_cols:
            cur.execute(f"ALTER TABLE ipo_master ADD COLUMN {c} {t}")
        conn.commit()
        print(f"[step5] 新加列: {[c for c, _ in new_cols]}")

    # 6) 计算 overhang_ratio + 写库
    updates = []
    samples = []
    for ipo_id, sc in rows:
        rec = results.get(_norm(sc or ""))
        if not rec:
            updates.append((None, None, None, ipo_id))
            continue
        pre = rec.get("pre_ipo_shares")
        post = rec.get("post_ipo_shares")
        ratio = (pre / post) if (pre and post and post > 0) else None
        updates.append((pre, post, ratio, ipo_id))
        if ratio is not None:
            samples.append(ratio)

    if samples:
        s = sorted(samples)
        n = len(s)
        print(f"  overhang_ratio: n={n} min={s[0]:.3f} p10={s[n//10]:.3f} "
              f"p50={s[n//2]:.3f} p90={s[(n*9)//10]:.3f} max={s[-1]:.3f}")

    if args.dry_run:
        print("\n[dry-run] 不写库 / 不刷 csv")
        conn.close()
        return 0

    cur.executemany("""
        UPDATE ipo_master
        SET pre_ipo_shares=?, post_ipo_shares=?, overhang_ratio=?
        WHERE ipo_id=?
    """, updates)
    conn.commit()

    # 7) 同步刷新本地 csv
    if not args.skip_csv:
        CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(CSV_OUT, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["thscode", "post_ipo_shares", "actual_issued_shares", "pre_ipo_shares"])
            for k in sorted(results.keys()):
                v = results[k]
                w.writerow([
                    v["thscode"],
                    v.get("post_ipo_shares") or "",
                    v.get("actual_issued_shares") or "",
                    v.get("pre_ipo_shares") or "",
                ])
        print(f"[step7] 同步刷新 {CSV_OUT.name}: {len(results)} 行")

    # 8) 验证
    print("\n--- 验证 ---")
    n_oh = cur.execute("SELECT COUNT(*) FROM ipo_master WHERE overhang_ratio IS NOT NULL").fetchone()[0]
    print(f"  overhang_ratio 非 NULL: {n_oh}/{len(rows)}")
    print(f"  unique: "
          f"{cur.execute('SELECT COUNT(DISTINCT overhang_ratio) FROM ipo_master').fetchone()[0]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
