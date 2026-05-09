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
    DELISTED_HK,
    NULL_TOKENS,
    parse_float,
    parse_int,
    parse_date,
    parse_str,
    make_ipo_id,
    make_cornerstone_id,
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
from nacs_model import CornerstoneType  # noqa: E402


# =============================================================================
# 汇率: 与 scripts/migrate_data_quality_v1.py 保持一致
# =============================================================================
FX_USD_HKD = 7.80
FX_CNY_HKD = 1.10


def _fx_to_hkd(currency: Optional[str]) -> float:
    c = (currency or "HKD").upper()
    if c == "USD":
        return FX_USD_HKD
    if c == "CNY":
        return FX_CNY_HKD
    return 1.0  # HKD or 未知 (回退保守)


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


def load_ipo_info(conn: sqlite3.Connection, csv_path: Path,
                  *, dry_run: bool = False,
                  overrides: Optional[Dict] = None) -> IpoLoadStats:
    """ifind_ipo_info.csv → ipo_master. 一行 = 一只 IPO.

    overrides: 由 load_overrides() 返回的 dict; None 表示自动按默认路径加载.
    """
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
        kwargs = {
            "ipo_id": ipo_id,
            "stock_code": stock_code,
            "company_name_zh": parse_str(row.get("company_name_zh")),
            "listing_date": listing_date,
            "pricing_date": parse_date(row.get("pricing_date")),
            "listing_chapter": chapter_override or "main_board",
            "offer_price_hkd": parse_float(row.get("offer_price_hkd")),
            "offer_price_high": parse_float(row.get("offer_price_high")),
            "offering_size_hkd": parse_float(row.get("offering_size_hkd")),
            "intl_oversub": parse_float(row.get("intl_oversub")),
            "public_oversub": parse_float(row.get("public_oversub")),
            "cornerstone_coverage": coverage,
            "data_source_notes": "ifind:p05310" + (
                "+overrides" if chapter_override or stock_code in
                ((overrides or {}).get("ipo_info") or {}) else ""
            ),
        }
        # 删掉 None 字段, 避免覆盖已存在的非空值
        kwargs = {k: v for k, v in kwargs.items()
                  if v is not None or k in ("ipo_id", "stock_code", "listing_date", "listing_chapter")}

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
        if currency_raw not in ("HKD", "USD", "CNY"):
            currency_raw = "HKD"  # 未知 — 保守不换算
        fx = _fx_to_hkd(currency_raw)
        native = parse_float(row.get("ticket_size_hkd"))
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


def load_delisted(conn: sqlite3.Connection, csv_path: Path,
                  *, dry_run: bool = False) -> DelistedLoadStats:
    """ifind_delisted_hk.csv → 更新 ipo_master 的退市标记字段.

    匹配键: stock_code (一只 IPO 唯一对应一个港股代码).
    若退市表给出 IPO 在我们 ipo_master 之外的 stock_code, 计入 n_unmatched (不写库).
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

        cur = conn.execute("""
            UPDATE ipo_master
            SET is_delisted = 1,
                delisting_date = ?,
                is_acquired = ?
            WHERE stock_code = ?
        """, (delist_date, is_acquired, stock_code))
        if cur.rowcount > 0:
            stats.n_matched += 1
        else:
            stats.n_unmatched += 1

    return stats


# =============================================================================
# CLI
# =============================================================================

KNOWN_TABLES = ("ipo", "cornerstones", "delisted")  # 当前覆盖范围


def parse_tables_arg(s: str) -> List[str]:
    if s.strip().lower() == "all":
        return list(KNOWN_TABLES)
    parts = [t.strip().lower() for t in s.split(",") if t.strip()]
    bad = [t for t in parts if t not in KNOWN_TABLES]
    if bad:
        raise SystemExit(f"未知的 --tables: {bad} (允许: {KNOWN_TABLES})")
    return parts


def main() -> int:
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

    print(f"[load_to_db] raw={raw_dir}")
    print(f"[load_to_db] db ={db_path}  dry_run={args.dry_run}")
    print(f"[load_to_db] tables={tables}")

    if args.init_db and not args.dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        init_database(str(db_path))
        print(f"[load_to_db] schema initialized")

    if not db_path.exists():
        if args.dry_run:
            print(f"[load_to_db] WARN: DB 不存在 ({db_path}), dry-run 用 :memory: 兜底")
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
    """实际执行各表 loader 并打印统计"""
    if "ipo" in tables:
        path = raw_dir / "ifind_ipo_info.csv"
        print(f"\n--- IPO info ({path.name}) ---")
        s = load_ipo_info(conn, path, dry_run=dry_run)  # type: ignore[arg-type]
        print(f"  rows in CSV : {s.n_rows_csv}")
        print(f"  upserted    : {s.n_inserted}")
        print(f"  skipped (no listing_date): {s.n_skipped_no_date}")
        print(f"  skipped (no stock_code) : {s.n_skipped_no_code}")

    if "cornerstones" in tables:
        path = raw_dir / "ifind_cornerstones.csv"
        print(f"\n--- Cornerstones ({path.name}) ---")
        s2 = load_cornerstones(conn, path, dry_run=dry_run)  # type: ignore[arg-type]
        print(f"  rows in CSV     : {s2.n_rows_csv}")
        print(f"  unique CS in run: {s2.n_cs_unique}")
        print(f"  CS new (first seen, DB had none): {s2.n_cs_new}")
        print(f"  CS preserved (DB already has, type kept): {s2.n_cs_preserved}")
        print(f"  aliases upserted: {s2.n_aliases_added}")
        print(f"  ipo x cs links  : {s2.n_links_inserted}")
        print(f"  skipped (incomplete row): {s2.n_skipped}")

    if "delisted" in tables:
        path = raw_dir / "ifind_delisted_hk.csv"
        print(f"\n--- Delisted ({path.name}) ---")
        if not path.exists():
            print(f"  ⚠ CSV 不存在 ({path}), 跳过")
            print(f"    → 先跑: python src/data_sources/ifind/delisted_pull.py")
        else:
            s3 = load_delisted(conn, path, dry_run=dry_run)  # type: ignore[arg-type]
            print(f"  rows in CSV : {s3.n_rows_csv}")
            print(f"  matched (ipo_master 中已有 stock_code): {s3.n_matched}")
            print(f"  unmatched (退市但 ipo_master 中无): {s3.n_unmatched}")
            print(f"  skipped (no stock_code): {s3.n_skipped}")


if __name__ == "__main__":
    raise SystemExit(main())
