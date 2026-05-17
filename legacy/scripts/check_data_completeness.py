"""
数据完整性校验: 通过 iFinD 拉取港股全量 IPO 数据, 与现有 DB + CSV 对比

输出:
  1. 控制台: 逐项对比报告 (新增 IPO、缺失基石、财务空洞等)
  2. CSV:    outputs/data_completeness_report.csv  (差异明细)

用法:
    python scripts/check_data_completeness.py
    python scripts/check_data_completeness.py --dry-run   # 不调 iFinD, 仅对比 CSV vs DB
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Windows GBK → UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from log import get_logger, setup_cli_logging

setup_cli_logging("INFO")
_log = get_logger("check_completeness")

DB_PATH = PROJECT_ROOT / "data" / "nacs_real.db"
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "ifind"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# iFinD 登录 + 拉取
# ============================================================================

def login_ifind() -> bool:
    from data_sources.ifind.market_env_fetcher import login_ifind as _login
    return _login()


def pull_ipo_info_from_ifind(sdate: str, edate: str):
    """THS_DR p05310 拉取全量 IPO 首发信息"""
    from iFinDPy import THS_DR

    all_fields = ",".join([f"p05310_f{i:03d}:Y" for i in range(1, 55)])
    ttype_str = f'ttype=1;sdate={sdate};edate={edate};sfzx=1'

    _log.info("拉取 p05310 (首发信息): %s ~ %s", sdate, edate)
    t0 = time.time()
    result = THS_DR('p05310', ttype_str, all_fields, 'format:dataframe')

    if result.errorcode != 0:
        _log.error("p05310 失败: %s", result.errmsg)
        return None

    df = result.data
    _log.info("p05310: %d 条, 耗时 %.1fs", len(df), time.time() - t0)
    return df


def pull_cornerstones_from_ifind(sdate: str, edate: str):
    """THS_DR p05309 拉取全量基石投资者"""
    from iFinDPy import THS_DR

    fields = (
        'p05309_f001:Y,p05309_f002:Y,p05309_f003:Y,p05309_f016:Y,'
        'p05309_f004:Y,p05309_f017:Y,p05309_f005:Y,p05309_f018:Y,'
        'p05309_f006:Y,p05309_f019:Y,p05309_f009:Y,p05309_f008:Y,'
        'p05309_f011:Y,p05309_f014:Y,p05309_f010:Y,p05309_f015:Y,'
        'p05309_f012:Y,p05309_f013:Y'
    )
    ttype_str = f'ttype=1;sdate={sdate};edate={edate};sfzx=1'

    _log.info("拉取 p05309 (基石投资者): %s ~ %s", sdate, edate)
    t0 = time.time()
    result = THS_DR('p05309', ttype_str, fields, 'format:dataframe')

    if result.errorcode != 0:
        _log.error("p05309 失败: %s", result.errmsg)
        return None

    df = result.data
    _log.info("p05309: %d 条, 耗时 %.1fs", len(df), time.time() - t0)
    return df


# ============================================================================
# DB / CSV 读取
# ============================================================================

def load_db_ipo_master() -> dict:
    """从 DB 读取 ipo_master, 返回 {stock_code: row_dict}"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT stock_code, company_name_zh, listing_date, pricing_date,
               listing_chapter, offer_price_hkd, offering_size_hkd,
               cornerstone_coverage, public_oversub, intl_oversub,
               status, is_delisted
        FROM ipo_master
    """).fetchall()
    result = {r["stock_code"]: dict(r) for r in rows}
    conn.close()
    return result


def load_db_cornerstone_links() -> list[dict]:
    """从 DB 读取 ipo_cornerstone_link"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT ipo_id, stock_code, cornerstone_name, ticket_size_hkd,
               lockup_months_actual, currency
        FROM ipo_cornerstone_link
    """).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result


def load_db_financials() -> set:
    """从 DB 读取 ipo_financials, 返回 {(stock_code, report_year)}"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT stock_code, report_year FROM ipo_financials").fetchall()
    result = {(r["stock_code"], r["report_year"]) for r in rows}
    conn.close()
    return result


def load_db_returns() -> set:
    """从 DB 读取有 returns 的 ipo_id, 再反查 stock_code"""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT m.stock_code
        FROM ipo_returns r
        JOIN ipo_master m ON r.ipo_id = m.ipo_id
    """).fetchall()
    result = {r[0] for r in rows}
    conn.close()
    return result


def load_csv_ipo_info() -> dict:
    """从 CSV 读取 ifind_ipo_info.csv, 返回 {stock_code: row_dict}"""
    path = RAW_DIR / "ifind_ipo_info.csv"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return {r["p05310_f001"]: r for r in rows if r.get("p05310_f001")}


def load_csv_cornerstones() -> list[dict]:
    """从 CSV 读取 ifind_cornerstones.csv"""
    path = RAW_DIR / "ifind_cornerstones.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ============================================================================
# 对比逻辑
# ============================================================================

def compare_ipo_info(ifind_df, csv_data: dict, db_data: dict) -> list[dict]:
    """对比 iFinD 实时数据 vs CSV vs DB, 返回差异列表"""
    issues = []

    # iFinD 返回的全量 stock_code
    if ifind_df is not None:
        ifind_codes = set()
        for _, row in ifind_df.iterrows():
            code = str(row.get("p05310_f001", "")).strip()
            if code and code.endswith(".HK"):
                ifind_codes.add(code)

        _log.info("iFinD 返回 %d 只 IPO", len(ifind_codes))
    else:
        ifind_codes = set(csv_data.keys())
        _log.info("使用 CSV 数据 (%d 只) 代替 iFinD 实时数据", len(ifind_codes))

    csv_codes = set(csv_data.keys())
    db_codes = set(db_data.keys())

    # 1) iFinD 有, CSV 无 (CSV 过时)
    new_in_ifind = ifind_codes - csv_codes
    for code in sorted(new_in_ifind):
        name = ""
        if ifind_df is not None:
            mask = ifind_df["p05310_f001"] == code
            if mask.any():
                name = str(ifind_df.loc[mask, "p05310_f002"].iloc[0])
        issues.append({
            "category": "CSV 缺失",
            "stock_code": code,
            "company_name": name,
            "detail": "iFinD 有数据但本地 CSV 无记录 (需更新 CSV)",
            "severity": "HIGH",
        })

    # 2) CSV 有, DB 无 (ETL 未入库)
    csv_not_in_db = csv_codes - db_codes
    for code in sorted(csv_not_in_db):
        row = csv_data[code]
        name = row.get("p05310_f002", "")
        listing_date = row.get("p05310_f033", "")
        issues.append({
            "category": "DB 缺失",
            "stock_code": code,
            "company_name": name,
            "detail": f"CSV 有但 DB 无 (listing_date={listing_date}, 需跑 load_to_db)",
            "severity": "HIGH",
        })

    # 3) DB 有但 iFinD/CSV 无 (幽灵记录)
    phantom = db_codes - ifind_codes
    for code in sorted(phantom):
        row = db_data[code]
        issues.append({
            "category": "幽灵记录",
            "stock_code": code,
            "company_name": row.get("company_name_zh", ""),
            "detail": "DB 有但 iFinD/CSV 无 (可能是手动录入或数据错误)",
            "severity": "LOW",
        })

    # 4) DB 中关键字段缺失
    for code, row in sorted(db_data.items()):
        missing_fields = []
        if row.get("offer_price_hkd") is None:
            missing_fields.append("offer_price_hkd")
        if row.get("offering_size_hkd") is None:
            missing_fields.append("offering_size_hkd")
        if row.get("cornerstone_coverage") is None:
            missing_fields.append("cornerstone_coverage")
        if row.get("pricing_date") is None:
            missing_fields.append("pricing_date")
        if row.get("intl_oversub") is None:
            missing_fields.append("intl_oversub")

        if missing_fields:
            issues.append({
                "category": "字段缺失",
                "stock_code": code,
                "company_name": row.get("company_name_zh", ""),
                "detail": f"DB 中缺失: {', '.join(missing_fields)}",
                "severity": "MEDIUM" if len(missing_fields) >= 3 else "LOW",
            })

    return issues


def compare_cornerstones(ifind_df, csv_data: list, db_links: list, db_ipos: dict) -> list[dict]:
    """对比基石投资者数据"""
    issues = []

    # 从 DB 统计每只 IPO 的基石数量
    db_cs_by_ipo = {}
    for link in db_links:
        code = link["stock_code"]
        db_cs_by_ipo.setdefault(code, []).append(link)

    # 从 CSV/iFinD 统计
    source_data = []
    if ifind_df is not None:
        for _, row in ifind_df.iterrows():
            source_data.append({
                "stock_code": str(row.get("p05309_f001", "")),
                "cornerstone_name": str(row.get("p05309_f005", "")),
            })
    else:
        for row in csv_data:
            source_data.append({
                "stock_code": row.get("p05309_f001", ""),
                "cornerstone_name": row.get("p05309_f005", ""),
            })

    source_cs_by_ipo = {}
    for item in source_data:
        code = item["stock_code"]
        if code:
            source_cs_by_ipo.setdefault(code, []).append(item)

    # 对比
    all_ipo_codes = set(source_cs_by_ipo.keys()) | set(db_cs_by_ipo.keys())
    for code in sorted(all_ipo_codes):
        src_count = len(source_cs_by_ipo.get(code, []))
        db_count = len(db_cs_by_ipo.get(code, []))

        if src_count > 0 and db_count == 0:
            issues.append({
                "category": "基石 DB 缺失",
                "stock_code": code,
                "company_name": db_ipos.get(code, {}).get("company_name_zh", ""),
                "detail": f"iFinD/CSV 有 {src_count} 个基石, DB 无记录",
                "severity": "HIGH",
            })
        elif abs(src_count - db_count) > 0:
            issues.append({
                "category": "基石数量不一致",
                "stock_code": code,
                "company_name": db_ipos.get(code, {}).get("company_name_zh", ""),
                "detail": f"iFinD/CSV: {src_count} 个基石, DB: {db_count} 个",
                "severity": "MEDIUM",
            })

    # 有基石覆盖率但无基石明细的 IPO
    for code, row in db_ipos.items():
        coverage = row.get("cornerstone_coverage")
        if coverage and float(coverage) > 0 and code not in db_cs_by_ipo:
            issues.append({
                "category": "基石明细缺失",
                "stock_code": code,
                "company_name": row.get("company_name_zh", ""),
                "detail": f"cornerstone_coverage={coverage} 但无基石明细 (ipo_cornerstone_link)",
                "severity": "MEDIUM",
            })

    return issues


def check_financials_coverage(db_ipos: dict, db_financials: set) -> list[dict]:
    """检查财务数据覆盖率"""
    issues = []
    for code, row in sorted(db_ipos.items()):
        listing_date = row.get("listing_date", "")
        if not listing_date:
            continue

        listing_year = int(listing_date[:4])
        # 每只 IPO 应有上市前 1-2 年 + 上市后各年的年报
        expected_years = list(range(max(2022, listing_year - 1), min(2026, listing_year + 2)))

        missing_years = [y for y in expected_years if (code, y) not in db_financials]
        if missing_years:
            issues.append({
                "category": "财务数据缺失",
                "stock_code": code,
                "company_name": row.get("company_name_zh", ""),
                "detail": f"缺少年报: {missing_years} (listing={listing_date})",
                "severity": "LOW",
            })

    return issues


def check_returns_coverage(db_ipos: dict, db_returns: set) -> list[dict]:
    """检查收益率数据覆盖率"""
    issues = []
    today = date.today().isoformat()
    for code, row in sorted(db_ipos.items()):
        listing_date = row.get("listing_date", "")
        if not listing_date or listing_date > today:
            continue  # 未上市的不检查
        if code not in db_returns:
            issues.append({
                "category": "收益率缺失",
                "stock_code": code,
                "company_name": row.get("company_name_zh", ""),
                "detail": f"已上市 (listing={listing_date}) 但 ipo_returns 无记录",
                "severity": "HIGH",
            })

    return issues


# ============================================================================
# 输出报告
# ============================================================================

def print_summary(all_issues: list[dict]) -> None:
    """打印汇总"""
    from collections import Counter

    by_cat = Counter(i["category"] for i in all_issues)
    by_sev = Counter(i["severity"] for i in all_issues)

    print("\n" + "=" * 70)
    print("  数据完整性校验报告")
    print("=" * 70)

    print(f"\n总问题数: {len(all_issues)}")
    print(f"  HIGH:   {by_sev.get('HIGH', 0)}")
    print(f"  MEDIUM: {by_sev.get('MEDIUM', 0)}")
    print(f"  LOW:    {by_sev.get('LOW', 0)}")

    print("\n按类别:")
    for cat, cnt in by_cat.most_common():
        print(f"  {cat}: {cnt}")

    # 打印 HIGH 级别明细
    high_issues = [i for i in all_issues if i["severity"] == "HIGH"]
    if high_issues:
        print(f"\n--- HIGH 级别明细 ({len(high_issues)} 条) ---")
        for i in high_issues[:30]:
            print(f"  [{i['category']}] {i['stock_code']} {i['company_name']}: {i['detail']}")
        if len(high_issues) > 30:
            print(f"  ... 还有 {len(high_issues) - 30} 条")

    # 打印 MEDIUM 级别前 10 条
    med_issues = [i for i in all_issues if i["severity"] == "MEDIUM"]
    if med_issues:
        print(f"\n--- MEDIUM 级别样例 (前 10/{len(med_issues)} 条) ---")
        for i in med_issues[:10]:
            print(f"  [{i['category']}] {i['stock_code']} {i['company_name']}: {i['detail']}")


def save_report_csv(all_issues: list[dict]) -> Path:
    """保存完整报告到 CSV"""
    out_path = OUTPUT_DIR / "data_completeness_report.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["severity", "category", "stock_code",
                                                "company_name", "detail"])
        writer.writeheader()
        for issue in sorted(all_issues, key=lambda x: (
            {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[x["severity"]],
            x["category"], x["stock_code"]
        )):
            writer.writerow(issue)
    return out_path


# ============================================================================
# CLI
# ============================================================================

def main():
    ap = argparse.ArgumentParser(description="港股 IPO 数据完整性校验")
    ap.add_argument("--dry-run", action="store_true",
                    help="不调 iFinD, 仅对比现有 CSV vs DB")
    ap.add_argument("--sdate", default="20220101", help="起始日期 (默认 20220101)")
    ap.add_argument("--edate", default=date.today().strftime("%Y%m%d"),
                    help="截止日期 (默认今天)")
    args = ap.parse_args()

    _log.info("数据完整性校验 — sdate=%s edate=%s dry_run=%s", args.sdate, args.edate, args.dry_run)

    # 1. 加载现有数据
    _log.info("加载现有 DB 数据...")
    db_ipos = load_db_ipo_master()
    db_links = load_db_cornerstone_links()
    db_financials = load_db_financials()
    db_returns = load_db_returns()
    _log.info("  DB: ipo_master=%d, links=%d, financials=%d, returns=%d",
              len(db_ipos), len(db_links), len(db_financials), len(db_returns))

    _log.info("加载现有 CSV 数据...")
    csv_ipos = load_csv_ipo_info()
    csv_cs = load_csv_cornerstones()
    _log.info("  CSV: ipo_info=%d, cornerstones=%d", len(csv_ipos), len(csv_cs))

    # 2. 拉取 iFinD 实时数据 (除非 dry-run)
    ifind_ipo_df = None
    ifind_cs_df = None

    if not args.dry_run:
        try:
            login_ifind()
            ifind_ipo_df = pull_ipo_info_from_ifind(args.sdate, args.edate)
            time.sleep(1)
            ifind_cs_df = pull_cornerstones_from_ifind(args.sdate, args.edate)
        except Exception as e:
            _log.warning("iFinD 调用失败 (%s), 退回 CSV 对比模式", e)

    # 3. 逐项对比
    all_issues = []

    _log.info("对比 IPO 首发信息...")
    all_issues.extend(compare_ipo_info(ifind_ipo_df, csv_ipos, db_ipos))

    _log.info("对比基石投资者...")
    all_issues.extend(compare_cornerstones(ifind_cs_df, csv_cs, db_links, db_ipos))

    _log.info("检查财务数据覆盖...")
    all_issues.extend(check_financials_coverage(db_ipos, db_financials))

    _log.info("检查收益率覆盖...")
    all_issues.extend(check_returns_coverage(db_ipos, db_returns))

    # 4. 输出
    print_summary(all_issues)
    out_path = save_report_csv(all_issues)
    _log.info("完整报告已保存: %s", out_path)

    # 5. 额外: 打印 iFinD vs CSV 差异汇总 (如果有 iFinD 数据)
    if ifind_ipo_df is not None:
        ifind_codes = set(
            str(row["p05310_f001"]).strip()
            for _, row in ifind_ipo_df.iterrows()
            if str(row.get("p05310_f001", "")).strip().endswith(".HK")
        )
        csv_codes = set(csv_ipos.keys())
        print(f"\n--- iFinD vs CSV 差异 ---")
        print(f"  iFinD 返回: {len(ifind_codes)} 只")
        print(f"  CSV 现有:   {len(csv_codes)} 只")
        new_codes = ifind_codes - csv_codes
        if new_codes:
            print(f"  新增 (iFinD有/CSV无): {len(new_codes)} 只")
            for c in sorted(new_codes):
                mask = ifind_ipo_df["p05310_f001"] == c
                if mask.any():
                    name = str(ifind_ipo_df.loc[mask, "p05310_f002"].iloc[0])
                    listing = str(ifind_ipo_df.loc[mask, "p05310_f033"].iloc[0])
                    print(f"    {c} {name} (listing={listing})")
        removed = csv_codes - ifind_codes
        if removed:
            print(f"  减少 (CSV有/iFinD无): {len(removed)} 只 → {sorted(removed)}")

    if ifind_cs_df is not None:
        print(f"\n--- 基石数据 iFinD vs CSV ---")
        print(f"  iFinD 返回: {len(ifind_cs_df)} 条基石记录")
        print(f"  CSV 现有:   {len(csv_cs)} 条")
        ifind_cs_ipos = set(
            str(row["p05309_f001"]).strip()
            for _, row in ifind_cs_df.iterrows()
            if str(row.get("p05309_f001", "")).strip().endswith(".HK")
        )
        csv_cs_ipos = set(r["p05309_f001"] for r in csv_cs if r.get("p05309_f001"))
        new_cs = ifind_cs_ipos - csv_cs_ipos
        if new_cs:
            print(f"  新增 IPO (有基石): {len(new_cs)} 只 → {sorted(new_cs)[:10]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
