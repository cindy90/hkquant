"""
Deal YAML 输入: 把已聆讯/招股阶段的项目落地到 ipo_master + ipo_cornerstone_link.

设计:
    每个 deal 一个 YAML 文件 (data/deals/<stock_code>.yaml).
    iFinD raw CSV 没有的字段 (招股说明书披露的细节、人工核实的基石名单) 来这里录入.
    load_deal() 把 YAML 内容写进 ipo_master (status='prospectus' 或 'pricing').

YAML 结构 (示例):

    stock_code: 1187.HK
    company_name_zh: 可孚医疗
    listing_chapter: a_plus_h
    company_type: profitable
    expected_listing_date: 2026-05-06
    prospectus_pdf_path: data/prospectus/1187_可孚医疗.pdf
    analyst_notes: |
      中信里昂主导, 路演反馈正面.
    ipo_master_overrides:
      offer_price_low: 7.50
      offer_price_high: 8.20
      greenshoe_pct: 0.15
      lockup_months: 6
      gics_l2: "医疗保健业(HS)-医疗设备及用品(HS)"
      sponsor_primary: 中信里昂
      sponsor_tier: 1
      pe_at_offer: 22.0
      pe_peer_median: 18.0
    cornerstones:
      - cornerstone_name: 蓝思科技(香港)有限公司
        cornerstone_type: strategic_industrial
        ticket_size_native: 78305000
        currency: HKD
        lockup_months: 6
        ultimate_holder: 蓝思科技股份有限公司
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# 允许覆盖到 ipo_master 的字段集合 (与 schema 对齐, 不允许 ipo_id / created_at 等元字段)
ALLOWED_MASTER_FIELDS = {
    "company_name_zh", "company_name_en", "listing_chapter", "is_a_h",
    "a_share_code", "a_share_adv_cny", "gics_l2",
    "offer_price_hkd", "offer_price_low", "offer_price_high",
    "offering_size_hkd", "gross_proceeds_excl_greenshoe", "total_offer_shares",
    "pricing_in_range", "intl_oversub", "public_oversub",
    "clawback_triggered", "greenshoe_pct", "greenshoe_exercised",
    "sponsor_primary", "sponsor_tier", "joint_sponsor_count", "auditor_tier",
    "pe_at_offer", "pe_peer_median", "last_round_premium",
    "cornerstone_total_hkd", "cornerstone_coverage", "cornerstone_count",
    "lockup_months",
    "pre_ipo_shares", "post_ipo_shares", "overhang_ratio",
    "peer_lockup_avg_drawdown", "pe_vs_history_pct", "fundamental_risk_score",
    "data_quality_score",
}

VALID_CORNERSTONE_TYPES = {
    "sovereign_pension", "global_long_only", "top_hedge_preipo",
    "cn_mutual_insurance", "strategic_industrial", "policy_fund",
    "pe_vc_continuation", "family_office_spv",
}

VALID_CHAPTERS = {
    "main_board_profitable", "main_board_unprofitable", "a_plus_h",
    "secondary", "18a", "18c_commercial", "18c_precommercial", "spac",
}


@dataclass
class DealLoadStats:
    deal_id: str = ""
    stock_code: str = ""
    ipo_master_action: str = ""        # 'inserted' / 'updated' / 'skipped'
    cornerstones_inserted: int = 0
    warnings: List[str] = field(default_factory=list)


def _make_ipo_id(stock_code: str, listing_date: str) -> str:
    """与 field_mappings.make_ipo_id 一致"""
    return f"HK_{stock_code.replace('.', '_')}_{listing_date[:4]}"


def _make_cornerstone_id(name: str) -> str:
    """与 field_mappings.make_cornerstone_id 一致"""
    bad = "()（）.,。， '\"-/\\"
    s = name.strip()
    for ch in bad:
        s = s.replace(ch, "_")
    while "__" in s:
        s = s.replace("__", "_")
    s = s.strip("_")
    return f"CS_{s}"


def lint_deal(data: Dict[str, Any]) -> List[str]:
    """返回错误列表; 空则通过."""
    errs: List[str] = []
    if not data.get("stock_code"):
        errs.append("missing required: stock_code")
    if not data.get("expected_listing_date"):
        errs.append("missing required: expected_listing_date (YYYY-MM-DD)")
    if not data.get("listing_chapter"):
        errs.append("missing required: listing_chapter")
    elif data["listing_chapter"] not in VALID_CHAPTERS:
        errs.append(f"invalid listing_chapter: {data['listing_chapter']} "
                    f"(allowed: {sorted(VALID_CHAPTERS)})")

    overrides = data.get("ipo_master_overrides") or {}
    if not isinstance(overrides, dict):
        errs.append("ipo_master_overrides 必须是 mapping")
    else:
        bad = [k for k in overrides if k not in ALLOWED_MASTER_FIELDS]
        if bad:
            errs.append(f"ipo_master_overrides 含不允许的字段: {bad}")

    cornerstones = data.get("cornerstones") or []
    if not isinstance(cornerstones, list):
        errs.append("cornerstones 必须是 list")
    else:
        for i, cs in enumerate(cornerstones):
            if not isinstance(cs, dict):
                errs.append(f"cornerstones[{i}] 必须是 mapping")
                continue
            if not cs.get("cornerstone_name"):
                errs.append(f"cornerstones[{i}] 缺少 cornerstone_name")
            ct = cs.get("cornerstone_type")
            if ct and ct not in VALID_CORNERSTONE_TYPES:
                errs.append(f"cornerstones[{i}] 不合法的 cornerstone_type: {ct}")
            curr = (cs.get("currency") or "HKD").upper()
            if curr not in ("HKD", "USD", "CNY"):
                errs.append(f"cornerstones[{i}] 不合法的 currency: {curr}")
    return errs


def load_deal_dict(conn: sqlite3.Connection, data: Dict[str, Any]) -> DealLoadStats:
    """把一份已 lint 通过的 deal dict 写进 DB.

    幂等: 同 stock_code 重复跑会 update ipo_master + upsert cornerstone links.
    """
    from data.dao import upsert_cornerstone, link_cornerstone_to_ipo, add_alias
    from data_sources.ifind.load_to_db import _classify_status, _fx_to_hkd

    errs = lint_deal(data)
    if errs:
        raise ValueError("deal lint failed:\n  " + "\n  ".join(errs))

    stock_code = data["stock_code"]
    expected_date = str(data["expected_listing_date"])  # 可能是 date 对象, 强转 str
    listing_chapter = data["listing_chapter"]
    ipo_id = _make_ipo_id(stock_code, expected_date)

    overrides = dict(data.get("ipo_master_overrides") or {})

    # status 根据 intl_oversub 是否已知自动定;
    # 但 manual deals 多数还在 prospectus 阶段, 默认 'prospectus' 除非数据完整
    intl_oversub = overrides.get("intl_oversub")
    today_iso = date.today().isoformat()
    deal_status = _classify_status(expected_date, intl_oversub, today_iso)

    stats = DealLoadStats(deal_id=stock_code, stock_code=stock_code)

    # ---- 1. ipo_master ----
    existing = conn.execute(
        "SELECT ipo_id, status FROM ipo_master WHERE stock_code = ?",
        (stock_code,),
    ).fetchone()

    cols = {
        "ipo_id": ipo_id,
        "stock_code": stock_code,
        "company_name_zh": data.get("company_name_zh"),
        "listing_date": expected_date,                    # 用预期日占位, 上市后 ETL 覆盖
        "expected_listing_date": expected_date,
        "listing_chapter": listing_chapter,
        "status": deal_status,
        "prospectus_pdf_path": data.get("prospectus_pdf_path"),
        "data_source_notes": "manual:deal_yaml",
    }
    cols.update({k: v for k, v in overrides.items() if v is not None})

    # 删 None (避免 ON CONFLICT 把已有非空值覆盖为 NULL)
    cols = {k: v for k, v in cols.items() if v is not None}

    placeholders = ", ".join("?" for _ in cols)
    col_str = ", ".join(cols.keys())
    update_str = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "ipo_id")
    conn.execute(
        f"INSERT INTO ipo_master ({col_str}) VALUES ({placeholders}) "
        f"ON CONFLICT(ipo_id) DO UPDATE SET {update_str}",
        list(cols.values()),
    )
    stats.ipo_master_action = "updated" if existing else "inserted"

    # ---- 2. cornerstones ----
    for cs in (data.get("cornerstones") or []):
        cs_name = cs["cornerstone_name"]
        cs_id = _make_cornerstone_id(cs_name)
        cs_type = cs.get("cornerstone_type") or "family_office_spv"

        upsert_cornerstone(
            conn,
            cornerstone_id=cs_id,
            canonical_name=cs_name,
            cornerstone_type=__import__("nacs_model").CornerstoneType(cs_type),
            country_of_origin=cs.get("country_of_origin"),
            aum_usd_latest=cs.get("aum_usd"),
            parent_entity=cs.get("ultimate_holder"),
            notes=cs.get("notes"),
        )
        # 别名: 让 raw CSV 里同一基石被识别为同一 cs_id
        add_alias(conn, cornerstone_id=cs_id, alias_text=cs_name,
                  alias_type="manual_deal", match_confidence=1.0)

        currency = (cs.get("currency") or "HKD").upper()
        fx = _fx_to_hkd(currency)
        native = cs.get("ticket_size_native") or cs.get("ticket_size_hkd")
        hkd = native * fx if native is not None else None

        link_cornerstone_to_ipo(
            conn,
            ipo_id=ipo_id,
            cornerstone_id=cs_id,
            stock_code=stock_code,
            cornerstone_name=cs_name,
            ticket_size_hkd=hkd,
            ticket_size_native=native,
            currency=currency,
            fx_to_hkd=fx,
            allocation_shares=cs.get("allocation_shares"),
            lockup_months_actual=cs.get("lockup_months"),
            data_source="manual:deal_yaml",
            is_estimated=bool(cs.get("is_estimated", False)),
            affiliation_flag=int(cs.get("affiliation_flag", 0)),
            affiliation_reason=cs.get("affiliation_reason"),
        )
        stats.cornerstones_inserted += 1

    return stats


def load_deal_file(conn: sqlite3.Connection, yaml_path: Path) -> DealLoadStats:
    """读 YAML 然后调 load_deal_dict. 主入口."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError("load_deal_file 需要 pyyaml") from e
    text = yaml_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{yaml_path}: 根节点必须是 mapping")
    return load_deal_dict(conn, data)
