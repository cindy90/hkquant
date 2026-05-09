"""
P1-#8 修复: 给 ipo_master 加 2 列 + 计算 LockupContext 数据.

背景:
    LockupContext 4 个字段在 build_offering 中全硬编码为常量, 导致 R_lockup
    的 60% 权重 (val_reversal/overhang/fund/peer) 退化为常数, 弱化区分度.

可补的字段 (其他 2 个无可靠数据源, 保留默认):
    1. fundamental_risk_score: 用上市前最近年报 (net_margin / roe) 反推
    2. pe_vs_history_pct: chapter 内, pricing_date 之前 pe_at_offer 排序百分位

不补的字段 (留默认):
    - overhang_ratio: 需要 pre/post_ipo_shares (DB 缺)
    - peer_lockup_avg_drawdown: 需要 price_history (现 0 行)

look-ahead 防御:
    - fundamental: report_year < pricing_date.year
    - pe_vs_history: 仅用 pricing_date 之前的同 chapter IPO

用法:
    python scripts/fix_p1_lockup_context.py --dry-run
    python scripts/fix_p1_lockup_context.py
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def compute_fundamental_risk(conn, sc, pricing_year, chapter):
    """0=低风险, 1=高风险. 18A/18C 用先验默认; 主板用最近年报."""
    if chapter == "18a":
        return 0.50  # 生物科技多无收入, 保留中性
    if chapter == "18c_precommercial":
        return 0.55
    if chapter == "18c_commercial":
        return 0.40

    # main_board_profitable / a_plus_h / secondary: 用最近年报
    fin = conn.execute("""
        SELECT net_margin, roe FROM ipo_financials
        WHERE stock_code=? AND report_year < ? AND net_margin IS NOT NULL
        ORDER BY report_year DESC LIMIT 1
    """, (sc, pricing_year)).fetchone()
    if not fin:
        return 0.50
    nm, roe = fin
    nm = nm if nm is not None else 0
    roe = roe if roe is not None else 0
    # 公式: net_margin/roe 都是 % (15 = 15%)
    if nm >= 15 and roe >= 15:
        return 0.10
    if nm >= 8 and roe >= 8:
        return 0.20
    if nm >= 3 and roe >= 3:
        return 0.35
    if nm >= 0 and roe >= 0:
        return 0.50
    if nm >= -10 and roe >= -10:
        return 0.65
    return 0.80


def compute_pe_pct(conn, current_pe, pricing_date, chapter):
    """同 chapter 中, pricing_date 之前 pe_at_offer 的百分位 [0, 1]."""
    if current_pe is None:
        return 0.50
    rows = conn.execute("""
        SELECT pe_at_offer FROM ipo_master
        WHERE listing_chapter=? AND pricing_date < ? AND pe_at_offer IS NOT NULL
    """, (chapter, pricing_date)).fetchall()
    pes = [r[0] for r in rows]
    if len(pes) < 5:
        return 0.50  # 历史太少, 中性
    rank = sum(1 for p in pes if p <= current_pe) / len(pes)
    return float(rank)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"DB 不存在: {DB}")
        return 2

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    # 1) ALTER TABLE 加 2 列 (idempotent)
    existing_cols = {r[1] for r in cur.execute("PRAGMA table_info(ipo_master)").fetchall()}
    new_cols = []
    if "fundamental_risk_score" not in existing_cols:
        new_cols.append("fundamental_risk_score REAL")
    if "pe_vs_history_pct" not in existing_cols:
        new_cols.append("pe_vs_history_pct REAL")
    if new_cols and not args.dry_run:
        for c in new_cols:
            cur.execute(f"ALTER TABLE ipo_master ADD COLUMN {c}")
        conn.commit()
        print(f"[step1] 新加列: {new_cols}")
    else:
        print(f"[step1] 列已存在或 dry-run: existing={existing_cols & {'fundamental_risk_score', 'pe_vs_history_pct'}}")

    # 2) 遍历计算
    rows = cur.execute("""
        SELECT ipo_id, stock_code, pricing_date, listing_chapter, pe_at_offer
        FROM ipo_master
    """).fetchall()
    print(f"[step2] 共 {len(rows)} 行 IPO")

    updates = []
    fund_dist = {}
    pe_pct_samples = []
    for ipo_id, sc, pd_str, chapter, pe in rows:
        if not pd_str:
            continue
        try:
            pd_d = date.fromisoformat(str(pd_str)[:10])
        except Exception:
            continue
        pricing_year = pd_d.year

        fund = compute_fundamental_risk(conn, sc, pricing_year, chapter)
        pe_pct = compute_pe_pct(conn, pe, pd_str, chapter)

        updates.append((fund, pe_pct, ipo_id))
        # 分布统计
        bucket = round(fund * 10) / 10
        fund_dist[bucket] = fund_dist.get(bucket, 0) + 1
        pe_pct_samples.append(pe_pct)

    print(f"\n[fundamental_risk_score 分布]")
    for k in sorted(fund_dist.keys()):
        print(f"  {k:.2f}: {fund_dist[k]} 行")

    if pe_pct_samples:
        sorted_pe = sorted(pe_pct_samples)
        n = len(sorted_pe)
        p10 = sorted_pe[n // 10]
        p50 = sorted_pe[n // 2]
        p90 = sorted_pe[(n * 9) // 10]
        n_05 = sum(1 for x in pe_pct_samples if x == 0.5)
        print(f"\n[pe_vs_history_pct] n={n} p10={p10:.2f} p50={p50:.2f} p90={p90:.2f}")
        print(f"  =0.5 (中性兜底): {n_05} 行 (来自历史不足或 pe NULL)")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        conn.close()
        return 0

    cur.executemany("""
        UPDATE ipo_master
        SET fundamental_risk_score=?, pe_vs_history_pct=?
        WHERE ipo_id=?
    """, updates)
    conn.commit()
    print(f"\n✅ UPDATE: {cur.rowcount} 行")

    # 3) 验证
    print("\n--- 验证 ---")
    n_fund = cur.execute(
        "SELECT COUNT(*) FROM ipo_master WHERE fundamental_risk_score IS NOT NULL"
    ).fetchone()[0]
    n_pe_pct = cur.execute(
        "SELECT COUNT(*) FROM ipo_master WHERE pe_vs_history_pct IS NOT NULL"
    ).fetchone()[0]
    print(f"  fundamental_risk_score 非 NULL: {n_fund}/{len(rows)}")
    print(f"  pe_vs_history_pct 非 NULL:      {n_pe_pct}/{len(rows)}")
    print(f"  fundamental_risk_score unique:  "
          f"{cur.execute('SELECT COUNT(DISTINCT fundamental_risk_score) FROM ipo_master').fetchone()[0]}")
    print(f"  pe_vs_history_pct unique:       "
          f"{cur.execute('SELECT COUNT(DISTINCT pe_vs_history_pct) FROM ipo_master').fetchone()[0]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
