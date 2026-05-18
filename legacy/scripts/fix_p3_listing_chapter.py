"""P3-#4 修正 ipo_master.listing_chapter (基于 iFinD 板块成分验证 + 上市前财务).

修正依据 (verify_listing_chapter_via_ifind.py 报告):
    - 12 只 -W 中概股 DB 错归 18a (Pre-revenue Biotech), 实际是 WVR 同股不同权;
      按上市前一年净利润 (THS_BD ni_attr_to_cs) 区分:
        盈利 → main_board_profitable
        亏损 → main_board_unprofitable
    - 3 只 De-SPAC 上市错归 main_board / 18a, 实际是 spac
    - 1 只英矽智能错归主板, 实际是 18a (AI 制药 Pre-revenue Biotech)

数据来源: scripts/verify_listing_chapter_via_ifind.py + 一次性 iFinD ni_attr_to_cs 查询.

用法:
    python scripts/fix_p3_listing_chapter.py --dry-run
    python scripts/fix_p3_listing_chapter.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 修正映射 (stock_code → new listing_chapter)
# 已盈利 -W: 极兔/明略/商米; 未盈利 -W: 9 只; SPAC: 3 只; 真 18A: 英矽
CORRECTIONS = [
    # 已盈利 -W (上市前一年净利润 > 0)
    ("1519.HK", "极兔速递-W",   "18a",                   "main_board_profitable"),
    ("2718.HK", "明略科技-W",   "18a",                   "main_board_profitable"),
    ("6810.HK", "商米科技-W",   "18a",                   "main_board_profitable"),
    # 未盈利 -W (上市前一年净利润 < 0)
    ("2390.HK", "知乎-W",       "18a",                   "main_board_unprofitable"),
    ("2423.HK", "贝壳-W",       "18a",                   "main_board_unprofitable"),
    ("2391.HK", "涂鸦智能-W",   "18a",                   "main_board_unprofitable"),
    ("2076.HK", "BOSS直聘-W",  "18a",                   "main_board_unprofitable"),
    ("9690.HK", "途虎-W",       "18a",                   "main_board_unprofitable"),
    ("9660.HK", "地平线机器人-W", "18a",                  "main_board_unprofitable"),
    ("2590.HK", "极智嘉-W",     "18a",                   "main_board_unprofitable"),
    ("2525.HK", "禾赛-W",       "18a",                   "main_board_unprofitable"),
    ("2026.HK", "小马智行-W",   "18a",                   "main_board_unprofitable"),
    # SPAC De-SPAC 上市
    ("6676.HK", "找钢网-W",     "18a",                   "spac"),
    ("2562.HK", "狮腾控股",     "main_board_profitable", "spac"),
    ("2665.HK", "图达通",       "main_board_profitable", "spac"),
    # 真 18A 错归主板
    ("3696.HK", "英矽智能",     "main_board_profitable", "18a"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    # 1) 备份
    if not args.dry_run:
        bak_path = DB.with_suffix(DB.suffix + ".bak_p3_chapter_" + time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(DB, bak_path)
        print(f"[backup] {bak_path.name}")

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 2) 校验当前 chapter 与映射"旧值" 一致, 防止误改
    print("\n[step1] 校验当前 listing_chapter 与映射旧值一致性")
    actual_old = {}
    mismatches = []
    for sc, _, old_c, new_c in CORRECTIONS:
        r = cur.execute("SELECT listing_chapter FROM ipo_master WHERE stock_code=?", (sc,)).fetchone()
        if r is None:
            mismatches.append((sc, "缺失", old_c))
        elif r[0] != old_c:
            mismatches.append((sc, r[0], old_c))
        actual_old[sc] = r[0] if r else None
    if mismatches:
        print("  ⚠ 校验失败 (DB 当前值与映射旧值不一致):")
        for sc, db_val, exp_old in mismatches:
            print(f"    {sc}: DB={db_val} expected={exp_old}")
        if not args.dry_run:
            print("  → 终止 (避免误改). 请人工确认.")
            conn.close()
            return 3
    else:
        print(f"  ✓ {len(CORRECTIONS)} 只全部匹配")

    # 3) 应用修正
    print(f"\n[step2] 应用修正:")
    by_new = defaultdict(int)
    for sc, name, old_c, new_c in CORRECTIONS:
        by_new[new_c] += 1
        print(f"  {sc:<10s} {name:<18s} {old_c} → {new_c}")
    print(f"\n  汇总: {dict(by_new)}")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        conn.close()
        return 0

    cur.executemany(
        "UPDATE ipo_master SET listing_chapter=? WHERE stock_code=?",
        [(new_c, sc) for sc, _, _, new_c in CORRECTIONS],
    )
    conn.commit()

    # 4) 验证写入后分布
    print("\n[step3] 验证修正后 ipo_master 章节分布:")
    for r in cur.execute("SELECT listing_chapter, COUNT(*) FROM ipo_master GROUP BY listing_chapter ORDER BY COUNT(*) DESC").fetchall():
        print(f"  {r[0]:<28s} n={r[1]}")
    print(f"  总数: {cur.execute('SELECT COUNT(*) FROM ipo_master').fetchone()[0]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
