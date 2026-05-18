"""
P1-#9 修复: 清理 ipo_master 中 stock_code 归一化重复的孤儿记录.

背景:
    109 个无 cornerstone link 的 IPO 中, 108 个是真实 csv 缺数据 (港股 IPO 不
    强制披露基石), 模型 veto 合理. 但 1 个是 ETL bug:
        ipo_master 同公司 (美的集团) 有两条记录:
          - HK_3296_HK_2026   sc=3296.HK   18 link  (主)
          - HK_03296_HK_2026  sc=03296.HK   0 link  (孤儿, 来自不同来源)
    P0-#2 时 stock_code 归一化只在 pricing_date 修复时做了, 没有合并 ipo_master
    的归一化重复.

策略:
    1. 找出 stock_code 归一化后重复的 ipo_id
    2. 选 link 数多的为主, 删除其他 (孤儿)
    3. 同时清理 ipo_returns / cornerstone_performance_asof / 等关联表中孤儿引用

用法:
    python scripts/fix_p1_dedupe_stock_code.py --dry-run
    python scripts/fix_p1_dedupe_stock_code.py
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
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


def _norm(code: str) -> str:
    if not isinstance(code, str):
        return code
    h, _, t = code.partition(".")
    h = h.lstrip("0") or "0"
    return h + ("." + t if t else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT ipo_id, stock_code, listing_date, company_name_zh FROM ipo_master"
    ).fetchall()
    by_norm = defaultdict(list)
    for r in rows:
        by_norm[_norm(r[1])].append(r)

    dups = {k: v for k, v in by_norm.items() if len(v) > 1}
    print(f"[step1] ipo_master 总行: {len(rows)}, 归一化重复: {len(dups)}")

    if not dups:
        print("[step1] 无归一化重复, 跳过")
        conn.close()
        return 0

    orphans = []  # 待删除 ipo_id
    for k, vs in dups.items():
        # 计算每条 link 数
        with_links = []
        for r in vs:
            n_link = cur.execute(
                "SELECT COUNT(*) FROM ipo_cornerstone_link WHERE ipo_id=?", (r[0],)
            ).fetchone()[0]
            with_links.append((r, n_link))
        # 按 link 数降序, 取首位为主
        with_links.sort(key=lambda x: -x[1])
        master = with_links[0]
        rest = with_links[1:]
        print(f"\n[group {k}]")
        print(f"  保留 (主): {master[0][0]}  sc={master[0][1]}  link={master[1]}  name={(master[0][3] or '')[:30]}")
        for r, n_link in rest:
            print(f"  删除 (孤儿): {r[0]}  sc={r[1]}  link={n_link}  name={(r[3] or '')[:30]}")
            orphans.append(r[0])

    if args.dry_run:
        print(f"\n[dry-run] 待删除 {len(orphans)} 行 ipo_master, 不写库")
        conn.close()
        return 0

    print(f"\n[step2] 真实删除 {len(orphans)} 个孤儿 ipo_id")

    # 关联表: 检查并清理孤儿引用
    related_tables = [
        ("ipo_cornerstone_link", "ipo_id"),
        ("ipo_returns", "ipo_id"),
        ("ipo_financials", None),  # ipo_financials 用 stock_code, 不直接关联 ipo_id
    ]

    for orphan in orphans:
        # ipo_cornerstone_link
        n = cur.execute(
            "DELETE FROM ipo_cornerstone_link WHERE ipo_id=?", (orphan,)
        ).rowcount
        if n > 0:
            print(f"  - link 表删除 {n} 行 (ipo_id={orphan})")
        # ipo_returns
        n = cur.execute(
            "DELETE FROM ipo_returns WHERE ipo_id=?", (orphan,)
        ).rowcount
        if n > 0:
            print(f"  - returns 表删除 {n} 行")
        # ipo_master
        cur.execute("DELETE FROM ipo_master WHERE ipo_id=?", (orphan,))
        print(f"  - master 表删除 ipo_id={orphan}")

    conn.commit()

    # 验证
    print("\n--- 验证 ---")
    n_master = cur.execute("SELECT COUNT(*) FROM ipo_master").fetchone()[0]
    n_no_cs = cur.execute("""
        SELECT COUNT(*) FROM ipo_master m
        LEFT JOIN ipo_cornerstone_link l ON l.ipo_id = m.ipo_id
        WHERE l.ipo_id IS NULL
    """).fetchone()[0]
    print(f"  ipo_master 总行: {n_master}")
    print(f"  无 cornerstone link 的 IPO: {n_no_cs} (修复前 109)")

    # 再次检查归一化重复
    rows2 = cur.execute("SELECT ipo_id, stock_code FROM ipo_master").fetchall()
    by_norm2 = defaultdict(list)
    for r in rows2:
        by_norm2[_norm(r[1])].append(r)
    dups2 = {k: v for k, v in by_norm2.items() if len(v) > 1}
    print(f"  剩余归一化重复: {len(dups2)} (期望 0)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
