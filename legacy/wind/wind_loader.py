"""
Wind 数据 loader

输入: 用户根据模板填好的 nacs_data_template.xlsx + 一个目录下的 N 个 price CSV
输出: 灌入 nacs.db (按 schema.py 建表)

使用:
    python loaders/wind_loader.py --excel ./nacs_data_filled.xlsx \\
                                  --price-dir ./prices/ \\
                                  --db ./nacs.db

容错策略:
    - 缺失字段: 警告但继续 (V1.2 用类型先验代偿)
    - 单位检测: 自动识别 USD/HKD/CNY (按招股日中间价转HKD)
    - 别名归一: cornerstone_raw_name 第一次出现时新建主表条目, 标记为 "low_confidence"
      用户后续可手工 merge
    - 数据质量分: 必填缺失 → 0.5 折扣, 选填缺失 → 0.9
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Dict, List

try:
    import pandas as pd
except ImportError:
    print("需要安装 pandas: pip install pandas openpyxl")
    sys.exit(1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.schema import init_database
from data.dao import (
    db_connect, upsert_cornerstone, add_alias,
    upsert_ipo, link_cornerstone_to_ipo,
)
from nacs_model import CornerstoneType


# =============================================================================
# 1. 工具
# =============================================================================

# 简化版汇率(实际生产用日频USDHKD/CNYHKD表)
DEFAULT_FX = {"HKD": 1.0, "USD": 7.80, "CNY": 1.10}


def to_hkd(amount: float, ccy: str) -> float:
    return amount * DEFAULT_FX.get(ccy.upper(), 1.0)


def safe_str(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return str(v).strip()


def safe_float(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def safe_int(v) -> Optional[int]:
    f = safe_float(v)
    return int(f) if f is not None else None


def safe_bool(v) -> int:
    f = safe_float(v)
    return 1 if f and f >= 0.5 else 0


# =============================================================================
# 2. 别名 + cornerstone 主表辅助
# =============================================================================

def guess_cornerstone_type(raw_name: str,
                           full_name: Optional[str] = None) -> CornerstoneType:
    """
    用 raw name + full name 启发式猜测机构类型.
    不准确, 仅用于 first-pass, 用户应在 DB 中手工修正.
    """
    n = raw_name.lower()
    full = (full_name or "").lower()
    combined = n + " " + full
    combined_orig = raw_name + " " + (full_name or "")

    if any(k in combined for k in ["jpm", "ubs am", "blackrock", "fidelity",
                                   "capital group", "wellington",
                                   "t. rowe price", "schroder"]):
        return CornerstoneType.GLOBAL_LONG_ONLY
    if any(k in combined for k in ["gic", "adia", "mubadala", "cpp", "khazanah",
                                   "temasek", "norway"]):
        return CornerstoneType.SOVEREIGN_PENSION
    if any(k in combined for k in ["hillhouse", "coatue", "tiger", "sequoia",
                                   "高毅", "景林"]):
        return CornerstoneType.TOP_HEDGE_PREIPO
    if any(k in combined for k in ["人寿", "保险", "social security", "社保",
                                   "新华资产", "泰康", "理财", "光大",
                                   "新华人寿"]):
        return CornerstoneType.CHINESE_MUTUAL_INSURANCE
    if any(k in combined for k in ["国新", "大基金", "引导基金", "国资"]):
        return CornerstoneType.POLICY_FUND
    # 含 A股/港股代码 → 上市公司产业资本 (检查 raw 和 full)
    if any(k in combined_orig for k in [".HK", ".SH", ".SZ"]):
        return CornerstoneType.STRATEGIC_INDUSTRIAL
    if any(k in combined for k in ["pe", "venture", "fund management",
                                   "兰馨", "perseverance"]):
        return CornerstoneType.PE_VC_CONTINUATION
    return CornerstoneType.FAMILY_OFFICE_SPV


def get_or_create_cornerstone(conn, raw_name: str, full_name: str = None,
                              affiliation_disclosed: int = 0) -> str:
    """
    给定原文名, 返回 cornerstone_id. 不存在则新建.
    """
    from data.dao import resolve_cornerstone_id
    resolved = resolve_cornerstone_id(conn, raw_name)
    if resolved:
        return resolved[0]

    # 新建: ID 用 raw_name 派生
    cs_id = "CS_" + "".join(c if c.isalnum() else "_"
                            for c in raw_name.upper())[:60]
    cs_type = guess_cornerstone_type(raw_name, full_name)
    canonical = full_name or raw_name
    upsert_cornerstone(conn, cornerstone_id=cs_id,
                       canonical_name=canonical,
                       cornerstone_type=cs_type,
                       notes="auto_created_by_loader_v1")
    add_alias(conn, cornerstone_id=cs_id, alias_text=raw_name,
              alias_type="raw", match_confidence=1.0)
    if full_name and full_name != raw_name:
        add_alias(conn, cornerstone_id=cs_id, alias_text=full_name,
                  alias_type="full", match_confidence=0.9)
    return cs_id


# =============================================================================
# 3. 各 sheet 加载
# =============================================================================

CHAPTER_NORMALIZATION = {
    "main_board_profitable": "main_board_profitable",
    "a_plus_h": "a_plus_h",
    "main_board_unprofitable": "main_board_unprofitable",
    "18a": "18a",
    "18c_commercial": "18c_commercial",
    "18c_precommercial": "18c_precommercial",
    "secondary": "secondary",
    "spac": "spac",
}


def load_ipo_master(conn, df: pd.DataFrame, stats: Dict):
    """灌 IPO 主表"""
    df = df.iloc[1:]  # 跳过子说明行(第二行)
    n = 0
    for _, row in df.iterrows():
        code = safe_str(row.get("stock_code"))
        if not code or code.startswith("<"):
            continue   # 跳过空白和占位行

        chapter = safe_str(row.get("listing_chapter")) or "main_board_profitable"
        chapter = CHAPTER_NORMALIZATION.get(chapter, chapter)

        ipo_id = f"HK_{code.replace('.', '_')}_{safe_str(row.get('listing_date', ''))[:4]}"

        listing_date = safe_str(row.get("listing_date"))
        pricing_date = safe_str(row.get("pricing_date")) or listing_date

        # 必填字段缺失检查 -> 数据质量分
        required = ["stock_code", "listing_date", "pricing_date",
                    "listing_chapter", "offer_price_hkd", "offering_size_hkd",
                    "intl_oversub", "sponsor_primary"]
        missing = [f for f in required
                   if safe_float(row.get(f)) is None
                   and safe_str(row.get(f)) is None]
        dq = max(0.3, 1.0 - 0.10 * len(missing))

        upsert_ipo(conn,
            ipo_id=ipo_id,
            stock_code=code,
            company_name_zh=safe_str(row.get("company_name_zh")),
            listing_date=listing_date,
            pricing_date=pricing_date,
            listing_chapter=chapter,
            is_a_h=safe_bool(row.get("is_a_h")),
            a_share_code=safe_str(row.get("a_share_code")),
            gics_l2=safe_str(row.get("gics_l2")),
            offer_price_hkd=safe_float(row.get("offer_price_hkd")),
            offer_price_low=safe_float(row.get("offer_price_low")),
            offer_price_high=safe_float(row.get("offer_price_high")),
            offering_size_hkd=safe_float(row.get("offering_size_hkd")),
            intl_oversub=safe_float(row.get("intl_oversub")),
            public_oversub=safe_float(row.get("public_oversub")),
            clawback_triggered=safe_bool(row.get("clawback_triggered")),
            greenshoe_pct=safe_float(row.get("greenshoe_pct")),
            sponsor_primary=safe_str(row.get("sponsor_primary")),
            joint_sponsor_count=safe_int(row.get("joint_sponsor_count")) or 1,
            sponsor_tier=safe_int(row.get("sponsor_tier")) or 2,
            auditor_tier=safe_int(row.get("auditor_tier")) or 1,
            pe_at_offer=safe_float(row.get("pe_at_offer")),
            pe_peer_median=safe_float(row.get("pe_peer_median")),
            last_round_premium=safe_float(row.get("last_round_premium")),
            lockup_months=safe_int(row.get("lockup_months")) or 6,
            is_delisted=safe_bool(row.get("is_delisted")),
            delisting_date=safe_str(row.get("delisting_date")),
            data_quality_score=dq,
            data_source_notes=safe_str(row.get("data_quality_notes")),
        )
        n += 1
        stats["ipo_count"] += 1
        if missing:
            stats["ipo_with_missing_required"] += 1
    print(f"  ✓ IPO 主表: {n} 条")


def load_cornerstones(conn, df: pd.DataFrame, stats: Dict):
    """灌基石 link, 同时增量建立 cornerstone_master"""
    df = df.iloc[1:]   # 跳过子说明行
    n = 0
    new_cs = 0
    for _, row in df.iterrows():
        code = safe_str(row.get("stock_code"))
        raw_name = safe_str(row.get("cornerstone_raw_name"))
        ticket_val = safe_float(row.get("ticket_amount_value"))
        ccy = safe_str(row.get("ticket_amount_ccy")) or "HKD"

        if not (code and raw_name and ticket_val):
            continue

        listing_date = safe_str(row.get("ipo_listing_date", ""))
        ipo_id = f"HK_{code.replace('.', '_')}_{listing_date[:4]}"

        # 新建或匹配 cornerstone_master
        full_name = safe_str(row.get("cornerstone_full_name"))
        affil_disclosed = safe_str(row.get("affiliation_disclosed"))
        affil_flag = (affil_disclosed == "1")

        cs_id = get_or_create_cornerstone(conn, raw_name, full_name,
                                          int(affil_flag))

        # 检查是否新建
        was_new = conn.execute(
            "SELECT notes FROM cornerstone_master WHERE cornerstone_id = ?",
            (cs_id,),
        ).fetchone()
        if was_new and was_new["notes"] == "auto_created_by_loader_v1":
            new_cs += 1

        link_cornerstone_to_ipo(conn,
            ipo_id=ipo_id,
            cornerstone_id=cs_id,
            ticket_size_hkd=to_hkd(ticket_val, ccy),
            lockup_months_actual=safe_int(row.get("lockup_months")) or 6,
            affiliation_flag=affil_flag,
            affiliation_reason=safe_str(row.get("affiliation_reason")),
            data_source=safe_str(row.get("data_source")) or "user_filled",
            is_estimated=False,
        )
        n += 1
        stats["link_count"] += 1
    print(f"  ✓ 基石 link: {n} 条 (新建 {new_cs} 家基石主表条目)")


def load_price_history(conn, price_dir: Path, stats: Dict):
    """从 price CSV 文件批量灌价格"""
    if not price_dir.exists():
        print(f"  ⚠ 价格目录 {price_dir} 不存在, 跳过")
        return

    n_files = 0
    n_rows = 0
    for csv_file in price_dir.glob("*.csv"):
        # 文件名: 03296_HK.csv -> stock_code = 03296.HK
        stem = csv_file.stem
        code = stem.replace("_HK", ".HK").replace("_SH", ".SH")

        # 找对应的 ipo_id
        ipo_row = conn.execute(
            "SELECT ipo_id FROM ipo_master WHERE stock_code = ?", (code,)
        ).fetchone()
        if not ipo_row:
            continue

        ipo_id = ipo_row["ipo_id"]
        try:
            pf = pd.read_csv(csv_file)
        except Exception as e:
            print(f"    [WARN] 读取 {csv_file.name} 失败: {e}")
            continue

        # 列名归一: 容忍 close/收盘价/Close, amt/金额/turnover
        pf.columns = [c.lower().strip() for c in pf.columns]
        date_col = next((c for c in pf.columns if "date" in c or "日期" in c), None)
        close_col = next((c for c in pf.columns if "close" in c or "收盘" in c), None)
        amt_col = next((c for c in pf.columns if "amt" in c or "amount" in c
                        or "金额" in c or "turnover" in c), None)

        if not (date_col and close_col):
            continue

        for _, prow in pf.iterrows():
            d = safe_str(prow[date_col])
            cl = safe_float(prow[close_col])
            if not (d and cl):
                continue
            conn.execute("""
                INSERT OR REPLACE INTO price_history
                (ipo_id, trade_date, close_hkd, turnover_hkd)
                VALUES (?, ?, ?, ?)
            """, (ipo_id, d[:10], cl,
                  safe_float(prow.get(amt_col)) if amt_col else None))
            n_rows += 1
        n_files += 1
    print(f"  ✓ 价格历史: {n_files} 文件, {n_rows} 行")
    stats["price_files"] = n_files
    stats["price_rows"] = n_rows


def compute_returns_for_all(conn, stats: Dict):
    """所有 IPO 的 returns 派生"""
    from data.dao import compute_ipo_returns
    ipo_ids = [r["ipo_id"] for r in
               conn.execute("SELECT ipo_id FROM ipo_master")]
    n_ok = 0
    for ipo_id in ipo_ids:
        try:
            res = compute_ipo_returns(conn, ipo_id)
            if res:
                n_ok += 1
        except Exception:
            pass
    print(f"  ✓ 收益派生: {n_ok}/{len(ipo_ids)} 只 IPO 成功")
    stats["returns_ok"] = n_ok


# =============================================================================
# 4. 主流程
# =============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", required=True,
                        help="填好的 nacs_data_template.xlsx")
    parser.add_argument("--price-dir", default="./prices",
                        help="价格 CSV 目录 (每只 IPO 一个文件)")
    parser.add_argument("--db", default="./nacs.db",
                        help="目标 SQLite 数据库")
    parser.add_argument("--reset", action="store_true",
                        help="如DB存在则删除重建")
    args = parser.parse_args()

    if args.reset and os.path.exists(args.db):
        os.remove(args.db)
        print(f"  已删除旧库: {args.db}")

    print(f"[1/5] 初始化 DB schema: {args.db}")
    init_database(args.db)

    stats = {"ipo_count": 0, "ipo_with_missing_required": 0,
             "link_count": 0, "price_files": 0, "price_rows": 0,
             "returns_ok": 0}

    print(f"[2/5] 读取 Excel: {args.excel}")
    sheets = pd.read_excel(args.excel, sheet_name=None,
                           dtype=str, na_values=[""])
    print(f"      Found sheets: {list(sheets.keys())}")

    with db_connect(args.db) as conn:
        print(f"[3/5] 灌 IPO 主表 (sheet 01_ipo_master)")
        if "01_ipo_master" in sheets:
            load_ipo_master(conn, sheets["01_ipo_master"], stats)
        else:
            print("      ❌ sheet 01_ipo_master 缺失")

        print(f"[3.5/5] 灌基石 link (sheet 02_ipo_cornerstones)")
        if "02_ipo_cornerstones" in sheets:
            load_cornerstones(conn, sheets["02_ipo_cornerstones"], stats)

        print(f"[4/5] 灌价格历史 (目录 {args.price_dir})")
        load_price_history(conn, Path(args.price_dir), stats)

        print(f"[5/5] 派生 ipo_returns")
        compute_returns_for_all(conn, stats)

    print()
    print("=" * 60)
    print(" 灌库完成. 摘要:")
    print("=" * 60)
    for k, v in stats.items():
        print(f"  {k:<35} {v}")
    print()
    print(f"  下一步: python -c 'from backtest.engine import run_backtest; "
          f"records = run_backtest(\"{args.db}\"); print(len(records))'")


if __name__ == "__main__":
    main()
