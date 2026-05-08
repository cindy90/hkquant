"""
NACS 基石性能缓存填充 - 一次性脚本

把所有基石 (cornerstone_master) × 季度化的 asof 日期 → 物化到
cornerstone_performance_asof 表中, 让回测时不需要每次实时计算.

在 dao.py 的 hydrate 逻辑里, 会自动用 "asof 之前 90 天内最近的 snapshot",
所以季度化的覆盖密度足够 (每季度 1 次, 90 天容差完美覆盖).

用法:
    python build_perf_cache.py
    python build_perf_cache.py --db data/nacs_real.db
"""
import argparse
import sys
import sqlite3
import time
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT / 'src'))

from data.dao import compute_cornerstone_perf_asof


def quarterly_dates(start_year: int, end_year: int):
    """生成每季度月初的 date 序列"""
    out = []
    for y in range(start_year, end_year + 1):
        for m in [1, 4, 7, 10]:
            out.append(date(y, m, 1))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(ROOT / 'data' / 'nacs_real.db'))
    parser.add_argument('--start-year', type=int, default=2021)
    parser.add_argument('--end-year', type=int, default=2026)
    parser.add_argument('--clear-existing', action='store_true',
                        help='清空旧 snapshot 重建')
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ DB 不存在: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if args.clear_existing:
        n_old = conn.execute("SELECT COUNT(*) FROM cornerstone_performance_asof").fetchone()[0]
        conn.execute("DELETE FROM cornerstone_performance_asof")
        conn.commit()
        print(f"清空旧 snapshot: {n_old} 行")

    asofs = quarterly_dates(args.start_year, args.end_year)
    cs_ids = [r["cornerstone_id"] for r in
              conn.execute("SELECT cornerstone_id FROM cornerstone_master").fetchall()]

    print(f"基石总数: {len(cs_ids)}")
    print(f"季度切点: {len(asofs)} ({asofs[0]} → {asofs[-1]})")
    print(f"预计写入: {len(cs_ids) * len(asofs):,} 行 (其中大量为空 snapshot)")
    print()

    n_total = 0
    n_meaningful = 0  # 有实际数据的 snapshot
    t0 = time.time()

    for i, asof in enumerate(asofs):
        asof_str = asof.isoformat()
        n_q = 0
        n_q_meaningful = 0

        for cs_id in cs_ids:
            perf = compute_cornerstone_perf_asof(conn, cs_id, asof)
            conn.execute("""
                INSERT OR REPLACE INTO cornerstone_performance_asof
                (cornerstone_id, as_of_date, ipo_count_5y, avg_m6_return_5y,
                 winrate_m6_5y, avg_d30_return_5y, lockup_discipline_score,
                 sector_expertise)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (cs_id, asof_str,
                  perf["ipo_count_5y"],
                  perf["avg_m6_return_5y"],
                  perf["winrate_m6_5y"],
                  perf["avg_d30_return_5y"],
                  perf["lockup_discipline_score"],
                  json.dumps(perf["sector_expertise_dict"])))
            n_q += 1
            if perf["ipo_count_5y"] > 0:
                n_q_meaningful += 1

        conn.commit()
        n_total += n_q
        n_meaningful += n_q_meaningful
        elapsed = time.time() - t0
        eta = elapsed / (i + 1) * (len(asofs) - i - 1)
        print(f"  [{i+1:>2}/{len(asofs)}] {asof_str}: 写入 {n_q} 行 "
              f"(其中 {n_q_meaningful} 有数据)  累计耗时 {elapsed:.1f}s  ETA {eta:.0f}s")

    conn.close()
    print(f"\n✓ 完成: 写入 {n_total:,} 行 ({n_meaningful:,} 有数据), 总耗时 {time.time()-t0:.1f}s")
    print(f"  (其余空 snapshot 用于 hydrate 时的 fallback 处理)")


if __name__ == "__main__":
    main()
