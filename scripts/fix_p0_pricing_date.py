"""
P0-#2 修复脚本: 用 ifind_ipo_info.csv 的 f028 (招股截止日 / 真实定价日)
重新写入 ipo_master.pricing_date.

背景:
    ETL 此前把 p05310_f032 (暗盘日) 当作 pricing_date, 导致 384/385 行
    pricing_date == listing_date, 让 cornerstone hydrate 切点失效, 引发 look-ahead.

字段含义 (经数据样本反推):
    f028 = 招股截止日 = 真实 pricing_date
    f032 = 暗盘日 (上市前 1 天)
    f033 = 正式上市日 (DB 中 listing_date 当前主要来源)

用法:
    python scripts/fix_p0_pricing_date.py --dry-run   # 预演, 不写库
    python scripts/fix_p0_pricing_date.py             # 真实写入
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预演, 不写库")
    args = ap.parse_args()

    if not DB.exists():
        print(f"❌ DB 不存在: {DB}")
        return 2
    if not CSV.exists():
        print(f"❌ CSV 不存在: {CSV}")
        return 2

    csv = pd.read_csv(CSV, encoding="utf-8-sig")
    sub = csv[["p05310_f001", "p05310_f028"]].rename(
        columns={"p05310_f001": "stock_code", "p05310_f028": "pricing_raw"}
    )
    # 转 ISO 日期
    sub["pricing_iso"] = pd.to_datetime(
        sub["pricing_raw"], errors="coerce"
    ).dt.date.astype(str)
    sub = sub[sub["pricing_iso"] != "NaT"].copy()

    # 归一化 stock_code: 去除前导 0 (DB 中混存 03296.HK / 3296.HK 两种格式)
    def _norm(code: str) -> str:
        if not isinstance(code, str):
            return code
        head, _, tail = code.partition(".")
        return head.lstrip("0").rjust(1, "0") + ("." + tail if tail else "")

    sub["stock_norm"] = sub["stock_code"].map(_norm)

    # 双键不冲突
    assert sub["stock_norm"].is_unique, "ipo_info.csv 中 stock_code (归一化后) 不唯一"

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    db_rows = cur.execute(
        "SELECT ipo_id, stock_code, listing_date, pricing_date FROM ipo_master"
    ).fetchall()
    db = pd.DataFrame(db_rows, columns=["ipo_id", "stock_code", "listing_date", "pricing_date"])
    db["stock_norm"] = db["stock_code"].map(_norm)

    merged = db.merge(sub[["stock_norm", "pricing_iso"]], on="stock_norm", how="left")
    merged["new_pricing_date"] = merged["pricing_iso"]

    # 兜底: csv 缺失或 pricing_date 异常 (>= listing_date) 时, 用 listing_date - 10 天
    # (中位数典型间隔 10 天). 仅在没有更好数据时使用.
    from datetime import datetime, timedelta
    def _fallback(listing: str) -> str:
        return (datetime.fromisoformat(listing) - timedelta(days=10)).date().isoformat()

    need_fallback = merged["new_pricing_date"].isna() | (
        merged["new_pricing_date"] >= merged["listing_date"]
    )
    merged.loc[need_fallback, "new_pricing_date"] = merged.loc[need_fallback, "listing_date"].map(_fallback)
    merged.loc[need_fallback, "_fallback_used"] = True
    print(f"[fallback] 用 listing_date - 10d 兜底: {need_fallback.sum()} 行")

    # 兜底之后 new_pricing_date 应该 100% 有值且 < listing_date
    still_bad = merged[merged["new_pricing_date"] >= merged["listing_date"]]
    assert len(still_bad) == 0, f"仍有 {len(still_bad)} 行兜底失败"

    valid = merged[merged["pricing_date"] != merged["new_pricing_date"]].copy()
    print(f"✓ 待更新: {len(valid)} 行")

    # 预览前 5
    print("\n样本 (前 5 行):")
    print(
        valid[["ipo_id", "stock_code", "listing_date", "pricing_date", "new_pricing_date"]]
        .head()
        .to_string(index=False)
    )

    # 与 listing_date 平均时差
    if len(valid) > 0:
        gap = (
            pd.to_datetime(valid["listing_date"]) - pd.to_datetime(valid["new_pricing_date"])
        ).dt.days
        print(
            f"\nlisting_date - new_pricing_date 间隔: "
            f"min={gap.min()} max={gap.max()} median={int(gap.median())} 天"
        )

    if args.dry_run:
        print("\n[dry-run] 不写库, 退出.")
        conn.close()
        return 0

    # 真实更新
    cur.executemany(
        "UPDATE ipo_master SET pricing_date = ? WHERE ipo_id = ?",
        [(r.new_pricing_date, r.ipo_id) for r in valid.itertuples()],
    )
    conn.commit()
    print(f"\n✅ 已更新 {cur.rowcount} 行 pricing_date")

    # 验证
    after_eq = cur.execute(
        "SELECT COUNT(*) FROM ipo_master WHERE pricing_date >= listing_date"
    ).fetchone()[0]
    print(f"   验证: pricing_date >= listing_date 异常行数 = {after_eq}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
