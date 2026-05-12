"""
iFinD raw CSV → SQLite ETL loader

输入: data/raw/ifind/ 下 6 张 CSV
输出: data/nacs_real.db (UPSERT, idempotent)

本 loader 覆盖 P0 核心两张表:
    1. ifind_ipo_info.csv      → ipo_master
    2. ifind_cornerstones.csv  → cornerstone_master + cornerstone_aliases
                                + ipo_cornerstone_link

延后处理 (上游 pull 脚本格式问题或 schema 未扩展):
    - ifind_blocks.csv         (列式 dump 需要重 pull)
    - ifind_financials_annual  (schema.py 暂无 ipo_financials 表)
    - ifind_share_capital      (schema.py 暂无 ipo_share_capital 表)
    - ifind_secondary_offerings(下一阶段)

设计要点:
    - 幂等: 全程 UPSERT, 重跑不重复
    - 保留人工标签: 已存在 cornerstone_master 记录的 type/notes 不被覆盖
    - --dry-run: 只解析 + 打印计数, 不写库
    - --tables: 选择性加载子集, 默认 all

CLI 示例:
    python -m data_sources.ifind.load_to_db --dry-run
    python -m data_sources.ifind.load_to_db --tables ipo,cornerstones
    python -m data_sources.ifind.load_to_db  # 全量
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# 让本脚本既可作为 module (-m) 也可直接 python 运行
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from data_sources.ifind.field_mappings import (  # noqa: E402
    P05309_CORNERSTONES,
    P05310_IPO_INFO,
    FINANCIALS_ANNUAL,
    DELISTED_HK,
    BLOCK_TO_CHAPTER,
    NULL_TOKENS,
    SUPPORTED_CURRENCIES,
    parse_float,
    parse_int,
    parse_date,
    parse_str,
    make_ipo_id,
    make_cornerstone_id,
    get_fx_rate,
)
from data_sources.ifind.overrides import (  # noqa: E402
    load_overrides,
    apply_ipo_overrides,
    lint_overrides,
)
from data.dao import (  # noqa: E402
    db_connect,
    upsert_cornerstone,
    add_alias,
    upsert_ipo,
    link_cornerstone_to_ipo,
)
from data.schema import init_database  # noqa: E402
from data.data_quality import (  # noqa: E402
    refresh_quality_scores,
    generate_quality_report,
    save_quality_report,
)
from nacs_model import CornerstoneType  # noqa: E402
from log import get_logger  # noqa: E402

_log = get_logger(__name__)


# =============================================================================
# 常量: listing_chapter 有效值 (与 nacs_model.ListingChapter 对应)
# =============================================================================
VALID_CHAPTERS = frozenset({
    "main_board", "main_board_profitable", "main_board_unprofitable",
    "a_plus_h", "18a", "secondary",
    "18c_commercial", "18c_precommercial", "spac",
})


# =============================================================================
# 汇率: 按日期查季度表 (via field_mappings.get_fx_rate)
# =============================================================================
# 向后兼容别名, 供 deal_loader 等外部模块 import
FX_USD_HKD = 7.80  # legacy default, 新代码请用 get_fx_rate()
FX_CNY_HKD = 1.10  # legacy default, 新代码请用 get_fx_rate()


def _fx_to_hkd(currency: Optional[str], asof_date: Optional[str] = None) -> float:
    """返回 1 unit currency → HKD 汇率.

    asof_date: ISO date, 传入时按季度查表; None 则用默认常数 (向后兼容).
    """
    return get_fx_rate(currency or "HKD", asof_date)


# =============================================================================
# 路径默认
# =============================================================================
_PROJECT_ROOT = _SRC.parent
DEFAULT_RAW_DIR = _PROJECT_ROOT / "data" / "raw" / "ifind"
DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "nacs_real.db"


# =============================================================================
# 通用 CSV 读取
# =============================================================================

def read_csv_dict(path: Path, field_map: Dict[str, str]) -> List[Dict[str, str]]:
    """读 CSV, 用 field_map 把 raw header (p05309_f001...) 映射到语义列名.

    未在 field_map 中的列会被丢弃 (避免 schema 噪音).
    返回每行一个 dict: {semantic_col: raw_value}
    """
    if not path.exists():
        raise FileNotFoundError(f"CSV 不存在: {path}")

    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            mapped: Dict[str, str] = {}
            for raw_col, raw_val in raw_row.items():
                sem_col = field_map.get(raw_col)
                if sem_col is None:
                    continue
                mapped[sem_col] = raw_val
            rows.append(mapped)
    return rows


# =============================================================================
# 1. ipo_info CSV → ipo_master
# =============================================================================

@dataclass
class IpoLoadStats:
    n_rows_csv: int = 0
    n_inserted: int = 0
    n_skipped_no_date: int = 0
    n_skipped_no_code: int = 0
    n_overrides_applied: int = 0
    n_status_prospectus: int = 0
    n_status_pricing: int = 0
    n_sanitized: int = 0          # 因校验而被置 NULL 的字段总次数
    n_chapter_defaulted: int = 0  # 使用默认 'main_board' 的 IPO 数
    n_chapter_invalid: int = 0    # listing_chapter 不在有效集合中, 回退 main_board


def _classify_status(listing_date_iso: str,
                     intl_oversub: Optional[float],
                     today_iso: str) -> str:
    """根据 listing_date + 数据完整度推断 deal 阶段.

    Rules:
        listing_date <= today                 → 'listed'   (default)
        listing_date  > today + intl_oversub None  → 'prospectus'
        listing_date  > today + intl_oversub 已有  → 'pricing'

    退市标记由 load_delisted() 单独处理 ('delisted').
    """
    if listing_date_iso <= today_iso:
        return "listed"
    return "pricing" if intl_oversub is not None else "prospectus"


def load_ipo_info(conn: sqlite3.Connection, csv_path: Path,
                  *, dry_run: bool = False,
                  overrides: Optional[Dict] = None,
                  asof_today: Optional[str] = None) -> IpoLoadStats:
    """ifind_ipo_info.csv → ipo_master. 一行 = 一只 IPO.

    overrides:    由 load_overrides() 返回的 dict; None 表示自动按默认路径加载.
    asof_today:   推断 status 时用的"今天"; None → 系统当前日期 (date.today()).
                  测试可以注入固定日期保证可重复.
    """
    from datetime import date as _date
    today_iso = asof_today or _date.today().isoformat()

    rows = read_csv_dict(csv_path, P05310_IPO_INFO)
    if overrides is None:
        overrides = load_overrides()
    n_before = sum(1 for r in rows
                   if r.get("stock_code") in (overrides.get("ipo_info") or {}))
    rows = apply_ipo_overrides(rows, overrides)
    stats = IpoLoadStats(n_rows_csv=len(rows), n_overrides_applied=n_before)

    for row in rows:
        stock_code = parse_str(row.get("stock_code"))
        listing_date = parse_date(row.get("listing_date"))

        if not stock_code:
            stats.n_skipped_no_code += 1
            continue
        if not listing_date:
            stats.n_skipped_no_date += 1
            continue

        ipo_id = make_ipo_id(stock_code, listing_date)

        # cornerstone_coverage CSV 是 % 数 (e.g. 35.76), 转 0-1 小数
        coverage_raw = parse_float(row.get("cornerstone_coverage"))
        coverage = (coverage_raw / 100.0) if coverage_raw is not None else None

        # public_oversub 在 CSV 是倍数 (e.g. 3972.67), 跟 schema 语义一致
        # listing_chapter 在 ipo_info CSV 中无字段, 给 'main_board' 默认;
        # overrides.yaml 可以指定具体章节 (spac/secondary 等)
        chapter_override = parse_str(row.get("listing_chapter"))
        intl_oversub = parse_float(row.get("intl_oversub"))
        deal_status = _classify_status(listing_date, intl_oversub, today_iso)
        if deal_status == "prospectus":
            stats.n_status_prospectus += 1
        elif deal_status == "pricing":
            stats.n_status_pricing += 1

        pricing_date = parse_date(row.get("pricing_date"))
        offer_price = parse_float(row.get("offer_price_hkd"))
        offer_price_high = parse_float(row.get("offer_price_high"))
        offer_price_low = parse_float(row.get("offer_price_low"))
        offering_size = parse_float(row.get("offering_size_hkd"))
        public_oversub = parse_float(row.get("public_oversub"))

        # --- 输入校验: 不合理值置 NULL + log warning ---
        if offer_price is not None and offer_price <= 0:
            _log.warning("%s offer_price_hkd=%.4f <= 0, 置 NULL", stock_code, offer_price)
            offer_price = None
            stats.n_sanitized += 1
        if pricing_date is not None and pricing_date > listing_date:
            _log.warning("%s pricing_date=%s > listing_date=%s, 置 NULL",
                         stock_code, pricing_date, listing_date)
            pricing_date = None
            stats.n_sanitized += 1
        if coverage is not None and (coverage < 0 or coverage > 1.0):
            _log.warning("%s cornerstone_coverage=%.4f 超出 [0,1], 置 NULL",
                         stock_code, coverage)
            coverage = None
            stats.n_sanitized += 1
        if intl_oversub is not None and intl_oversub < 0:
            _log.warning("%s intl_oversub=%.2f < 0, 置 NULL", stock_code, intl_oversub)
            intl_oversub = None
            stats.n_sanitized += 1
        if public_oversub is not None and public_oversub < 0:
            _log.warning("%s public_oversub=%.2f < 0, 置 NULL", stock_code, public_oversub)
            public_oversub = None
            stats.n_sanitized += 1
        if offering_size is not None and offering_size <= 0:
            _log.warning("%s offering_size_hkd=%.2f <= 0, 置 NULL", stock_code, offering_size)
            offering_size = None
            stats.n_sanitized += 1
        if offer_price_high is not None and offer_price is not None \
                and offer_price_high < offer_price:
            _log.warning("%s offer_price_high=%.4f < offer_price=%.4f, high 置 NULL",
                         stock_code, offer_price_high, offer_price)
            offer_price_high = None
            stats.n_sanitized += 1
        if offer_price_low is not None and offer_price is not None \
                and offer_price_low > offer_price:
            _log.warning("%s offer_price_low=%.4f > offer_price=%.4f, low 置 NULL",
                         stock_code, offer_price_low, offer_price)
            offer_price_low = None
            stats.n_sanitized += 1

        total_offer_shares = parse_float(row.get("total_offer_shares"))

        # --- 章节校验 ---
        chapter = chapter_override or "main_board"
        if chapter not in VALID_CHAPTERS:
            _log.warning("%s listing_chapter=%r 不在有效集合, 回退 main_board",
                         stock_code, chapter)
            chapter = "main_board"
            stats.n_chapter_invalid += 1
        if chapter == "main_board" and not chapter_override:
            stats.n_chapter_defaulted += 1

        kwargs = {
            "ipo_id": ipo_id,
            "stock_code": stock_code,
            "company_name_zh": parse_str(row.get("company_name_zh")),
            "listing_date": listing_date,
            "pricing_date": pricing_date,
            "listing_chapter": chapter,
            "offer_price_hkd": offer_price,
            "offer_price_high": offer_price_high,
            "offer_price_low": offer_price_low,
            "offering_size_hkd": offering_size,
            "total_offer_shares": total_offer_shares,
            "intl_oversub": intl_oversub,
            "public_oversub": public_oversub,
            "cornerstone_coverage": coverage,
            "status": deal_status,
            "data_source_notes": "ifind:p05310" + (
                "+overrides" if chapter_override or stock_code in
                ((overrides or {}).get("ipo_info") or {}) else ""
            ),
        }
        # 删掉 None 字段, 避免覆盖已存在的非空值
        # status 总是带上 (NOT NULL with default, 但显式覆盖更清晰)
        kwargs = {k: v for k, v in kwargs.items()
                  if v is not None or k in ("ipo_id", "stock_code", "listing_date",
                                            "listing_chapter", "status")}

        if not dry_run:
            upsert_ipo(conn, **kwargs)
        stats.n_inserted += 1

    return stats


# =============================================================================
# 2. cornerstones CSV → cornerstone_master + aliases + link
# =============================================================================

@dataclass
class CornerstoneLoadStats:
    n_rows_csv: int = 0
    n_cs_unique: int = 0
    n_cs_new: int = 0          # 本次首次见, DB 也无 → 写入
    n_cs_preserved: int = 0    # DB 已有 → 不覆盖 type, 保留人工标签
    n_aliases_added: int = 0
    n_links_inserted: int = 0
    n_skipped: int = 0
    n_sanitized: int = 0       # 因校验而被置 NULL 的字段总次数
    n_unknown_currency: int = 0  # 未知 currency fallback 次数


def _existing_cornerstone_ids(conn: sqlite3.Connection) -> Set[str]:
    """已在库中的 cornerstone_master.cornerstone_id, 用于跳过 type 覆盖"""
    rows = conn.execute("SELECT cornerstone_id FROM cornerstone_master").fetchall()
    return {r["cornerstone_id"] for r in rows}


def load_cornerstones(conn: sqlite3.Connection, csv_path: Path,
                      *, dry_run: bool = False) -> CornerstoneLoadStats:
    """ifind_cornerstones.csv → cs_master + aliases + link.

    一行 CSV = 一只 IPO × 一个基石 (多基石 → 多行).
    """
    rows = read_csv_dict(csv_path, P05309_CORNERSTONES)
    stats = CornerstoneLoadStats(n_rows_csv=len(rows))

    # 即使 dry_run 也读取 DB 现有 cs_id, 以便准确反映"会被保留 vs 会被新建"
    existing_ids: Set[str] = (
        _existing_cornerstone_ids(conn) if conn is not None else set()
    )
    seen_cs_in_run: Set[str] = set()

    for row in rows:
        stock_code = parse_str(row.get("stock_code"))
        listing_date = parse_date(row.get("listing_date"))
        cs_name_raw = parse_str(row.get("cornerstone_name"))

        if not (stock_code and listing_date and cs_name_raw):
            stats.n_skipped += 1
            continue

        ipo_id = make_ipo_id(stock_code, listing_date)
        cs_id = make_cornerstone_id(cs_name_raw)

        # ----- cornerstone_master -----
        if cs_id in existing_ids:
            # DB 已有 → 不覆盖 type/notes (保留人工 promote)
            if cs_id not in seen_cs_in_run:
                stats.n_cs_preserved += 1
        else:
            # DB 没有 → 首见时写入, 后续重复跳过
            if cs_id not in seen_cs_in_run:
                if not dry_run:
                    upsert_cornerstone(
                        conn,
                        cornerstone_id=cs_id,
                        canonical_name=cs_name_raw,
                        cornerstone_type=CornerstoneType.FAMILY_OFFICE_SPV,  # 保守默认
                        notes=parse_str(row.get("cornerstone_desc")),
                    )
                stats.n_cs_new += 1
        seen_cs_in_run.add(cs_id)

        # ----- aliases -----
        # 把原文名作为 alias; 用 INSERT OR IGNORE, 重复无副作用
        if not dry_run:
            add_alias(
                conn,
                cornerstone_id=cs_id,
                alias_text=cs_name_raw,
                alias_type="prospectus",
                match_confidence=1.0,
            )
        stats.n_aliases_added += 1

        # ----- ipo_cornerstone_link -----
        # 货币归一: raw CSV 的 ticket_size_hkd 列实际是 native 值 (currency 列另给).
        # 写库时 ticket_size_native = native, ticket_size_hkd = native × FX.
        currency_raw = (parse_str(row.get("currency")) or "HKD").upper()
        if currency_raw not in SUPPORTED_CURRENCIES:
            _log.warning("%s/%s currency=%r 不在支持列表 %s, fallback HKD (不换算)",
                         stock_code, cs_name_raw, currency_raw,
                         sorted(SUPPORTED_CURRENCIES))
            currency_raw = "HKD"
            stats.n_unknown_currency += 1
        fx = _fx_to_hkd(currency_raw, listing_date)
        native = parse_float(row.get("ticket_size_hkd"))
        # 校验: ticket_size 不应为负
        if native is not None and native <= 0:
            _log.warning("%s/%s ticket_size=%.2f <= 0, 置 NULL",
                         stock_code, cs_name_raw, native)
            native = None
            stats.n_sanitized += 1
        hkd_normalized = native * fx if native is not None else None

        if not dry_run:
            link_cornerstone_to_ipo(
                conn,
                ipo_id=ipo_id,
                cornerstone_id=cs_id,
                stock_code=stock_code,
                cornerstone_name=cs_name_raw,
                ticket_size_hkd=hkd_normalized,
                ticket_size_native=native,
                currency=currency_raw,
                fx_to_hkd=fx,
                allocation_shares=parse_int(row.get("allocation_shares")),
                lockup_months_actual=parse_int(row.get("lockup_months")),
                data_source="ifind:p05309",
            )
        stats.n_links_inserted += 1

    stats.n_cs_unique = len(seen_cs_in_run)
    return stats


# =============================================================================
# 3. delisted CSV → ipo_master.is_delisted/delisting_date/is_acquired
# =============================================================================

@dataclass
class DelistedLoadStats:
    n_rows_csv: int = 0
    n_matched: int = 0    # 在 ipo_master 找到对应 stock_code
    n_unmatched: int = 0  # 退市表里有, ipo_master 中无 (该 IPO 不在 universe)
    n_skipped: int = 0
    n_date_invalid: int = 0  # 退市日早于上市日 (数据异常)


def load_delisted(conn: sqlite3.Connection, csv_path: Path,
                  *, dry_run: bool = False) -> DelistedLoadStats:
    """ifind_delisted_hk.csv → 更新 ipo_master 的退市标记字段.

    匹配键: stock_code (一只 IPO 唯一对应一个港股代码).
    若退市表给出 IPO 在我们 ipo_master 之外的 stock_code, 计入 n_unmatched (不写库).

    交叉验证: 退市日 vs 上市日 — 退市日 ≤ 上市日则记为 n_date_invalid 但仍写入.
    """
    rows = read_csv_dict(csv_path, DELISTED_HK)
    stats = DelistedLoadStats(n_rows_csv=len(rows))

    for row in rows:
        stock_code = parse_str(row.get("stock_code"))
        delist_date = parse_date(row.get("delisting_date"))
        if not stock_code:
            stats.n_skipped += 1
            continue
        is_acq_raw = parse_int(row.get("is_acquired"))
        is_acquired = 1 if (is_acq_raw and is_acq_raw > 0) else 0

        if dry_run:
            stats.n_matched += 1  # dry-run 不区分是否真匹配
            continue

        # 交叉验证: 退市日 vs 上市日
        if delist_date and conn is not None:
            listing_row = conn.execute(
                "SELECT listing_date FROM ipo_master WHERE stock_code = ?",
                (stock_code,),
            ).fetchone()
            if listing_row and listing_row["listing_date"]:
                # DB listing_date 可能是 date 对象或 str, 统一为 str 比较
                ld = listing_row["listing_date"]
                ld_str = ld.isoformat() if hasattr(ld, "isoformat") else str(ld)
                if delist_date <= ld_str:
                    _log.warning(
                        "%s delisting_date=%s <= listing_date=%s, 数据异常 (仍写入)",
                        stock_code, delist_date, ld_str,
                    )
                    stats.n_date_invalid += 1

        cur = conn.execute("""
            UPDATE ipo_master
            SET is_delisted = 1,
                delisting_date = ?,
                is_acquired = ?,
                status = 'delisted'
            WHERE stock_code = ?
        """, (delist_date, is_acquired, stock_code))
        if cur.rowcount > 0:
            stats.n_matched += 1
        else:
            stats.n_unmatched += 1
            _log.debug("退市表 %s 不在 ipo_master (universe 外)", stock_code)

    return stats


# =============================================================================
# 章节验证: 检查 ipo_master.listing_chapter 一致性
# =============================================================================

@dataclass
class ChapterValidationResult:
    total_ipos: int = 0
    n_valid: int = 0
    n_defaulted_main_board: int = 0  # 可能未经分类的 main_board
    n_invalid_chapter: int = 0       # 不在 VALID_CHAPTERS 中
    chapter_distribution: Dict = None  # type: ignore[assignment]
    issues: List = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.chapter_distribution is None:
            self.chapter_distribution = {}
        if self.issues is None:
            self.issues = []


def validate_chapters(conn: sqlite3.Connection) -> ChapterValidationResult:
    """校验 ipo_master 中 listing_chapter 的有效性和覆盖率.

    检查项:
      1. listing_chapter 值必须在 VALID_CHAPTERS 中
      2. 'main_board' 占比过高 (>90%) 提示可能未分类
      3. 统计章节分布
    """
    result = ChapterValidationResult()

    # 统计总数和分布
    rows = conn.execute(
        "SELECT listing_chapter, COUNT(*) AS cnt FROM ipo_master GROUP BY listing_chapter"
    ).fetchall()

    for row in rows:
        ch = row["listing_chapter"]
        cnt = row["cnt"]
        result.total_ipos += cnt
        result.chapter_distribution[ch] = cnt

        if ch not in VALID_CHAPTERS:
            result.n_invalid_chapter += cnt
            result.issues.append(
                f"无效 listing_chapter={ch!r} ({cnt} 只 IPO)"
            )
        elif ch == "main_board":
            result.n_defaulted_main_board += cnt
        else:
            result.n_valid += cnt

    # main_board 占比警告
    if result.total_ipos > 0:
        mb_pct = result.n_defaulted_main_board / result.total_ipos
        if mb_pct > 0.90:
            result.issues.append(
                f"main_board 占比 {mb_pct:.0%} (>{90}%), "
                "可能有大量 IPO 未经章节分类 (18A/18C/AH/SPAC 等)"
            )

    # 检查 -W 后缀股票被标为 18a 的潜在错分
    w_as_18a = conn.execute("""
        SELECT stock_code, company_name_zh FROM ipo_master
        WHERE listing_chapter = '18a'
          AND company_name_zh LIKE '%-W'
    """).fetchall()
    if w_as_18a:
        codes = [r["stock_code"] for r in w_as_18a]
        result.issues.append(
            f"{len(codes)} 只 -W 后缀股票被标为 18a, 可能需要区分: "
            f"{codes[:5]}{'...' if len(codes) > 5 else ''}"
        )

    return result


# =============================================================================
# 4. financials_annual CSV → ipo_financials
# =============================================================================

@dataclass
class FinancialsLoadStats:
    n_rows_csv: int = 0
    n_upserted: int = 0
    n_skipped_no_code: int = 0
    n_skipped_no_year: int = 0
    n_all_null: int = 0       # 所有财务字段均为 NULL (如新上市公司无历史)


def load_financials(conn: sqlite3.Connection, csv_path: Path,
                    *, dry_run: bool = False) -> FinancialsLoadStats:
    """ifind_financials_annual.csv → ipo_financials.

    一行 = 一个 stock_code × 一个 report_year.
    CSV 表头已是语义名 (thscode, total_oi, ...), 用 FINANCIALS_ANNUAL 映射.
    """
    rows = read_csv_dict(csv_path, FINANCIALS_ANNUAL)
    stats = FinancialsLoadStats(n_rows_csv=len(rows))

    for row in rows:
        stock_code = parse_str(row.get("stock_code"))
        report_year = parse_int(row.get("report_year"))

        if not stock_code:
            stats.n_skipped_no_code += 1
            continue
        if report_year is None:
            stats.n_skipped_no_year += 1
            continue

        revenue = parse_float(row.get("revenue"))
        gross_margin = parse_float(row.get("gross_margin"))
        net_margin = parse_float(row.get("net_margin"))
        roe = parse_float(row.get("roe"))

        # 全部为 NULL 的行 (iFinD 返回空) 计入但仍写入, 保留"已查询过"记录
        if all(v is None for v in (revenue, gross_margin, net_margin, roe)):
            stats.n_all_null += 1

        if not dry_run:
            conn.execute("""
                INSERT INTO ipo_financials (stock_code, report_year,
                                            revenue_cny, gross_margin, net_margin, roe)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_code, report_year) DO UPDATE SET
                    revenue_cny  = COALESCE(excluded.revenue_cny,  revenue_cny),
                    gross_margin = COALESCE(excluded.gross_margin, gross_margin),
                    net_margin   = COALESCE(excluded.net_margin,   net_margin),
                    roe          = COALESCE(excluded.roe,          roe)
            """, (stock_code, report_year, revenue, gross_margin, net_margin, roe))
        stats.n_upserted += 1

    return stats


# =============================================================================
# CLI
# =============================================================================

KNOWN_TABLES = ("ipo", "cornerstones", "delisted", "financials")


def parse_tables_arg(s: str) -> List[str]:
    if s.strip().lower() == "all":
        return list(KNOWN_TABLES)
    parts = [t.strip().lower() for t in s.split(",") if t.strip()]
    bad = [t for t in parts if t not in KNOWN_TABLES]
    if bad:
        raise SystemExit(f"未知的 --tables: {bad} (允许: {KNOWN_TABLES})")
    return parts


def main() -> int:
    from log import setup_cli_logging
    setup_cli_logging("INFO")

    ap = argparse.ArgumentParser(
        description="iFinD raw CSV → nacs_real.db ETL loader"
    )
    ap.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR),
                    help=f"CSV 目录 (默认: {DEFAULT_RAW_DIR})")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH),
                    help=f"目标 SQLite (默认: {DEFAULT_DB_PATH})")
    ap.add_argument("--tables", default="all",
                    help=f"加载哪些表, 逗号分隔 (默认: all = {KNOWN_TABLES})")
    ap.add_argument("--dry-run", action="store_true",
                    help="只解析+计数, 不写库")
    ap.add_argument("--init-db", action="store_true",
                    help="先 init schema (新库时用)")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    db_path = Path(args.db)
    tables = parse_tables_arg(args.tables)

    _log.info("raw=%s", raw_dir)
    _log.info("db=%s  dry_run=%s", db_path, args.dry_run)
    _log.info("tables=%s", tables)

    if args.init_db and not args.dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        init_database(str(db_path))
        _log.info("schema initialized")

    if not db_path.exists():
        if args.dry_run:
            _log.warning("DB 不存在 (%s), dry-run 用 :memory: 兜底", db_path)
        else:
            raise SystemExit(
                f"DB 不存在: {db_path}\n  → 用 --init-db 创建, 或检查路径"
            )

    # dry-run 也开真实 conn (写调用已被 if not dry_run 守护),
    # 这样 existing_ids 查询能反映真实 preserved 数.
    # DB 不存在时 (仅 dry-run 路径), 用 :memory: 临时建空 schema 兜底.
    if db_path.exists():
        with db_connect(str(db_path)) as conn:
            _run_loaders(conn, raw_dir, tables, dry_run=args.dry_run)
    else:
        from data.schema import SCHEMA_SQL
        with db_connect(":memory:") as conn:
            conn.executescript(SCHEMA_SQL)
            _run_loaders(conn, raw_dir, tables, dry_run=args.dry_run)

    return 0


def _run_loaders(conn: Optional[sqlite3.Connection], raw_dir: Path,
                 tables: List[str], *, dry_run: bool) -> None:
    """实际执行各表 loader 并输出统计"""
    if "ipo" in tables:
        path = raw_dir / "ifind_ipo_info.csv"
        _log.info("--- IPO info (%s) ---", path.name)
        s = load_ipo_info(conn, path, dry_run=dry_run)  # type: ignore[arg-type]
        _log.info("  rows_csv=%d upserted=%d skipped_no_date=%d skipped_no_code=%d "
                  "sanitized=%d chapter_defaulted=%d chapter_invalid=%d",
                  s.n_rows_csv, s.n_inserted, s.n_skipped_no_date, s.n_skipped_no_code,
                  s.n_sanitized, s.n_chapter_defaulted, s.n_chapter_invalid)

    if "cornerstones" in tables:
        path = raw_dir / "ifind_cornerstones.csv"
        _log.info("--- Cornerstones (%s) ---", path.name)
        s2 = load_cornerstones(conn, path, dry_run=dry_run)  # type: ignore[arg-type]
        _log.info("  rows_csv=%d unique_cs=%d new=%d preserved=%d aliases=%d links=%d "
                  "skipped=%d sanitized=%d unknown_currency=%d",
                  s2.n_rows_csv, s2.n_cs_unique, s2.n_cs_new, s2.n_cs_preserved,
                  s2.n_aliases_added, s2.n_links_inserted, s2.n_skipped,
                  s2.n_sanitized, s2.n_unknown_currency)

    if "delisted" in tables:
        path = raw_dir / "ifind_delisted_hk.csv"
        _log.info("--- Delisted (%s) ---", path.name)
        if not path.exists():
            _log.warning("CSV 不存在 (%s), 跳过; 先跑 delisted_pull.py", path)
        else:
            s3 = load_delisted(conn, path, dry_run=dry_run)  # type: ignore[arg-type]
            _log.info("  rows_csv=%d matched=%d unmatched=%d skipped=%d date_invalid=%d",
                      s3.n_rows_csv, s3.n_matched, s3.n_unmatched, s3.n_skipped,
                      s3.n_date_invalid)

    if "financials" in tables:
        path = raw_dir / "ifind_financials_annual.csv"
        _log.info("--- Financials (%s) ---", path.name)
        if not path.exists():
            _log.warning("CSV 不存在 (%s), 跳过; 先跑 full_data_pull.py", path)
        else:
            s4 = load_financials(conn, path, dry_run=dry_run)  # type: ignore[arg-type]
            _log.info("  rows_csv=%d upserted=%d skipped_no_code=%d "
                      "skipped_no_year=%d all_null=%d",
                      s4.n_rows_csv, s4.n_upserted, s4.n_skipped_no_code,
                      s4.n_skipped_no_year, s4.n_all_null)

    # ----- 数据质量评分 & 报告 -----
    if not dry_run and conn is not None:
        _log.info("--- Data Quality ---")
        refresh_quality_scores(conn)
        report = generate_quality_report(conn)
        save_quality_report(report)
        _log.info("  avg_score=%.4f  distribution=%s",
                  report.get("avg_quality_score") or 0,
                  report.get("score_distribution"))


if __name__ == "__main__":
    raise SystemExit(main())
