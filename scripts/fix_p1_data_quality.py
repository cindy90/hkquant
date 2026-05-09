"""
P1 数据质量三连修:

#3 pricing_in_range: 全 0.7 常量 → 用 (price - low) / (high - low) 重算
#4 greenshoe_pct  : max=53 单位错误 → 异常值置 NULL
#5 pe_at_offer    : ±4000 极值废值 → 截尾到 [-100, 200]

依赖:
    - data/raw/ifind/ifind_ipo_info.csv 中:
        f008 = offer_price_high
        f009 = offer_price_low (此前 ETL 漏映射)
        f010 = offer_price_hkd
    - 同时把 field_mappings.py 漏掉的 f009 也补上 (本脚本不动代码,
      只动 DB. 代码补在后续手工 edit.)

用法:
    python scripts/fix_p1_data_quality.py --dry-run
    python scripts/fix_p1_data_quality.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nacs_real.db"
CSV = ROOT / "data" / "raw" / "ifind" / "ifind_ipo_info.csv"

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _norm(code: str) -> str:
    """归一化 stock_code: 0300.HK / 00300.HK / 300.HK → 300.HK"""
    if not isinstance(code, str):
        return code
    head, _, tail = code.partition(".")
    head = head.lstrip("0") or "0"
    return head + ("." + tail if tail else "")


def _to_float(x):
    if x is None or (isinstance(x, str) and x.strip() in ("--", "")):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    csv = pd.read_csv(CSV, encoding="utf-8-sig")
    sub = csv[["p05310_f001", "p05310_f008", "p05310_f009", "p05310_f010"]].copy()
    sub.columns = ["stock_code", "high_raw", "low_raw", "price_raw"]
    sub["stock_norm"] = sub["stock_code"].map(_norm)
    sub["offer_price_high"] = sub["high_raw"].map(_to_float)
    sub["offer_price_low"] = sub["low_raw"].map(_to_float)
    sub["offer_price_hkd"] = sub["price_raw"].map(_to_float)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    db_rows = cur.execute(
        "SELECT ipo_id, stock_code, offer_price_low, offer_price_high, "
        "offer_price_hkd, pricing_in_range, greenshoe_pct, pe_at_offer "
        "FROM ipo_master"
    ).fetchall()
    db = pd.DataFrame(
        db_rows,
        columns=[
            "ipo_id", "stock_code", "old_low", "old_high",
            "old_price", "old_pir", "old_gs", "old_pe",
        ],
    )
    db["stock_norm"] = db["stock_code"].map(_norm)
    m = db.merge(
        sub[["stock_norm", "offer_price_high", "offer_price_low", "offer_price_hkd"]],
        on="stock_norm", how="left",
    )

    # ============================================================
    # #3 pricing_in_range: 用 (price - low) / (high - low) 重算
    # ============================================================
    def _compute_pir(row):
        lo, hi, p = row["offer_price_low"], row["offer_price_high"], row["offer_price_hkd"]
        if lo is None or hi is None or p is None:
            return None
        if hi <= lo:  # 一价定 (low == high) 或反常 → 0.5 中性
            return 0.5
        pir = (p - lo) / (hi - lo)
        return max(0.0, min(1.0, pir))

    m["new_pir"] = m.apply(_compute_pir, axis=1)
    n_pir_computed = m["new_pir"].notna().sum()
    n_pir_unchanged = (m["new_pir"] == m["old_pir"]).sum()

    # 分布
    if n_pir_computed > 0:
        pir_dist = m["new_pir"].dropna().describe()
        print("[#3 pricing_in_range] 重算分布:")
        print(f"  count={int(pir_dist['count'])} mean={pir_dist['mean']:.3f} "
              f"std={pir_dist['std']:.3f} min={pir_dist['min']:.3f} max={pir_dist['max']:.3f}")
        print(f"  unique 取值数: {m['new_pir'].nunique()}")
        # bucket
        for lo, hi in [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]:
            n = ((m["new_pir"] >= lo) & (m["new_pir"] < hi)).sum()
            print(f"  [{lo:.1f}, {hi:.2f}): {n}")
    print(f"  待写入: {n_pir_computed} 行 (其中 {n_pir_unchanged} 与原值同 = 0.7 中性)")

    # ============================================================
    # #4 greenshoe_pct: 异常值 (>0.30) → NULL, 让 build_offering 用 0.15 兜底
    # ============================================================
    GS_MAX_VALID = 0.30  # 港股绿鞋上限通常 15%, 极少超过 20%, 30% 是宽限
    bad_gs_mask = m["old_gs"].notna() & (m["old_gs"] > GS_MAX_VALID)
    n_gs_to_null = int(bad_gs_mask.sum())
    print(f"\n[#4 greenshoe_pct] 异常 (>{GS_MAX_VALID}): {n_gs_to_null} 行 → NULL")
    if n_gs_to_null > 0:
        sample_gs = m[bad_gs_mask].head(5)[["stock_code", "old_gs"]]
        print(sample_gs.to_string(index=False))

    # ============================================================
    # #5 pe_at_offer: 截尾到 [-100, 200], 外的置 NULL
    # ============================================================
    PE_LO, PE_HI = -100.0, 200.0
    bad_pe_mask = m["old_pe"].notna() & ((m["old_pe"] < PE_LO) | (m["old_pe"] > PE_HI))
    n_pe_to_null = int(bad_pe_mask.sum())
    print(f"\n[#5 pe_at_offer] 越界 [{PE_LO}, {PE_HI}]: {n_pe_to_null} 行 → NULL")
    if n_pe_to_null > 0:
        sample_pe = m[bad_pe_mask].head(8)[["stock_code", "old_pe"]]
        print(sample_pe.to_string(index=False))

    # ============================================================
    # 同时把 offer_price_low/high/hkd 三列从 csv 灌满 (现状全 NULL)
    # ============================================================
    n_low = m["offer_price_low"].notna().sum()
    n_high = m["offer_price_high"].notna().sum()
    n_price = m["offer_price_hkd"].notna().sum()
    print(f"\n[bonus] 价格三件套补齐: low={n_low} high={n_high} hkd={n_price}")

    if args.dry_run:
        print("\n[dry-run] 不写库")
        conn.close()
        return 0

    # ============================================================
    # 真实写入
    # ============================================================
    # 1) 价格三列 + pricing_in_range
    upd_price = [
        (
            r.offer_price_low if pd.notna(r.offer_price_low) else None,
            r.offer_price_high if pd.notna(r.offer_price_high) else None,
            r.offer_price_hkd if pd.notna(r.offer_price_hkd) else None,
            r.new_pir if pd.notna(r.new_pir) else None,
            r.ipo_id,
        )
        for r in m.itertuples()
    ]
    cur.executemany(
        "UPDATE ipo_master SET offer_price_low=?, offer_price_high=?, "
        "offer_price_hkd=COALESCE(?, offer_price_hkd), pricing_in_range=COALESCE(?, pricing_in_range) "
        "WHERE ipo_id=?",
        upd_price,
    )
    print(f"\n✅ #3 价格 + pricing_in_range: 更新 {cur.rowcount} 行")

    # 2) greenshoe_pct: 异常 → NULL
    bad_gs_ids = m.loc[bad_gs_mask, "ipo_id"].tolist()
    if bad_gs_ids:
        cur.executemany(
            "UPDATE ipo_master SET greenshoe_pct=NULL WHERE ipo_id=?",
            [(x,) for x in bad_gs_ids],
        )
        print(f"✅ #4 greenshoe_pct → NULL: {cur.rowcount} 行")

    # 3) pe_at_offer: 越界 → NULL
    bad_pe_ids = m.loc[bad_pe_mask, "ipo_id"].tolist()
    if bad_pe_ids:
        cur.executemany(
            "UPDATE ipo_master SET pe_at_offer=NULL WHERE ipo_id=?",
            [(x,) for x in bad_pe_ids],
        )
        print(f"✅ #5 pe_at_offer → NULL: {cur.rowcount} 行")

    conn.commit()

    # ============================================================
    # 验证
    # ============================================================
    print("\n--- 验证 ---")
    n_pir_const = cur.execute(
        "SELECT COUNT(DISTINCT pricing_in_range) FROM ipo_master "
        "WHERE pricing_in_range IS NOT NULL"
    ).fetchone()[0]
    print(f"  pricing_in_range unique 值: {n_pir_const} (修复前 = 1)")
    n_gs_bad = cur.execute(
        "SELECT COUNT(*) FROM ipo_master WHERE greenshoe_pct > 0.30"
    ).fetchone()[0]
    print(f"  greenshoe_pct > 0.30: {n_gs_bad} (期望 0)")
    n_pe_bad = cur.execute(
        "SELECT COUNT(*) FROM ipo_master "
        "WHERE pe_at_offer < -100 OR pe_at_offer > 200"
    ).fetchone()[0]
    print(f"  pe_at_offer 越界: {n_pe_bad} (期望 0)")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
