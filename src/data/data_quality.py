"""
ipo_master 数据质量评分与报告

设计:
    data_quality_score = 非空核心字段数 / 核心字段总数 (per row)
    核心字段 = NACS 三层模型评估必需的字段 (不含派生/审计/元数据字段)

    ETL 在每次 load/upsert 后调用 refresh_quality_scores() 批量更新;
    generate_quality_report() 输出全库摘要 (JSON-serializable dict).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from log import get_logger

_log = get_logger(__name__)


# NACS 评估依赖的核心字段 (按 schema.py ipo_master 列名)
# 缺一个字段 → 模型某层评分退化为中性 / 跳过, 会影响 NACS 准确性.
CORE_FIELDS: List[str] = [
    "stock_code",
    "company_name_zh",
    "listing_date",
    "listing_chapter",
    "offer_price_hkd",
    "offering_size_hkd",
    "total_offer_shares",
    "intl_oversub",
    "public_oversub",
    "cornerstone_coverage",
    "cornerstone_count",
    "sponsor_primary",
    "sponsor_tier",
    "pe_at_offer",
    "pe_peer_median",
]

# 按 NACS 三层模型拆分的字段分组
# L1 (Q_company): 发行质量 — 发行结构 + 估值 + 中介
L1_FIELDS: List[str] = [
    "listing_chapter",
    "offer_price_hkd",
    "offering_size_hkd",
    "total_offer_shares",
    "pricing_in_range",
    "intl_oversub",
    "public_oversub",
    "sponsor_primary",
    "sponsor_tier",
    "pe_at_offer",
    "pe_peer_median",
]

# L2 (Q_ecosystem): 基石生态 — ipo_master 聚合 + link 表细节
L2_FIELDS: List[str] = [
    "cornerstone_coverage",
    "cornerstone_count",
    "cornerstone_total_hkd",
]

# L3 (R_lockup): 锁定期风险 — 需要估值/股本/市场数据
L3_FIELDS: List[str] = [
    "lockup_months",
    "overhang_ratio",
    "peer_lockup_avg_drawdown",
    "pe_vs_history_pct",
    "fundamental_risk_score",
]

LAYER_GROUPS: Dict[str, List[str]] = {
    "L1_company": L1_FIELDS,
    "L2_ecosystem": L2_FIELDS,
    "L3_lockup": L3_FIELDS,
}


def compute_row_quality(row: Dict[str, Any]) -> float:
    """计算单行 data_quality_score (0..1).

    row: ipo_master 的 dict/Row. 只检查 CORE_FIELDS 中出现的列.
    """
    present = 0
    total = len(CORE_FIELDS)
    for f in CORE_FIELDS:
        val = row.get(f) if isinstance(row, dict) else row[f] if f in row.keys() else None
        if val is not None and str(val).strip() != "":
            present += 1
    return round(present / total, 4) if total > 0 else 1.0


def refresh_quality_scores(conn: sqlite3.Connection) -> int:
    """批量更新 ipo_master.data_quality_score.

    Returns: 被更新的行数.
    """
    # 用 SQL CASE 表达式在数据库端直接计算, 避免逐行 round-trip
    parts = []
    for f in CORE_FIELDS:
        parts.append(f"CASE WHEN {f} IS NOT NULL AND TRIM({f}) != '' THEN 1 ELSE 0 END")
    score_expr = "ROUND((" + " + ".join(parts) + f") * 1.0 / {len(CORE_FIELDS)}, 4)"

    sql = f"UPDATE ipo_master SET data_quality_score = {score_expr}"
    cur = conn.execute(sql)
    n = cur.rowcount
    _log.info("data_quality_score 更新 %d 行", n)
    return n


def generate_quality_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    """生成全库数据质量摘要报告 (JSON-serializable).

    返回结构:
        {
          "total_ipos": int,
          "avg_quality_score": float,
          "score_distribution": {"1.0": n, "0.8-0.99": n, ...},
          "field_coverage": {"stock_code": 1.0, "pe_at_offer": 0.65, ...},
          "worst_ipos": [{ipo_id, stock_code, score}, ...],  # score 最低 10 只
        }
    """
    report: Dict[str, Any] = {}

    # 总行数 + 平均分
    row = conn.execute(
        "SELECT COUNT(*) AS n, AVG(data_quality_score) AS avg_q "
        "FROM ipo_master"
    ).fetchone()
    report["total_ipos"] = row["n"]
    report["avg_quality_score"] = round(row["avg_q"], 4) if row["avg_q"] else None

    # 分档分布
    buckets = conn.execute("""
        SELECT
            SUM(CASE WHEN data_quality_score = 1.0 THEN 1 ELSE 0 END)   AS perfect,
            SUM(CASE WHEN data_quality_score >= 0.8
                      AND data_quality_score < 1.0 THEN 1 ELSE 0 END)   AS good,
            SUM(CASE WHEN data_quality_score >= 0.6
                      AND data_quality_score < 0.8 THEN 1 ELSE 0 END)   AS fair,
            SUM(CASE WHEN data_quality_score >= 0.4
                      AND data_quality_score < 0.6 THEN 1 ELSE 0 END)   AS poor,
            SUM(CASE WHEN data_quality_score < 0.4 THEN 1 ELSE 0 END)   AS critical
        FROM ipo_master
    """).fetchone()
    report["score_distribution"] = {
        "perfect_1.0": buckets["perfect"] or 0,
        "good_0.8-0.99": buckets["good"] or 0,
        "fair_0.6-0.79": buckets["fair"] or 0,
        "poor_0.4-0.59": buckets["poor"] or 0,
        "critical_<0.4": buckets["critical"] or 0,
    }

    # 每字段覆盖率 (核心字段)
    field_coverage: Dict[str, float] = {}
    total = report["total_ipos"]
    if total > 0:
        for f in CORE_FIELDS:
            r2 = conn.execute(
                f"SELECT COUNT(*) AS n FROM ipo_master "
                f"WHERE {f} IS NOT NULL AND TRIM(CAST({f} AS TEXT)) != ''"
            ).fetchone()
            field_coverage[f] = round(r2["n"] / total, 4)
    report["field_coverage"] = field_coverage

    # 按模型层级 (L1/L2/L3) 细分覆盖率
    layer_quality: Dict[str, Any] = {}
    if total > 0:
        for layer_name, fields in LAYER_GROUPS.items():
            layer_cov: Dict[str, float] = {}
            for f in fields:
                r3 = conn.execute(
                    f"SELECT COUNT(*) AS n FROM ipo_master "
                    f"WHERE {f} IS NOT NULL AND TRIM(CAST({f} AS TEXT)) != ''"
                ).fetchone()
                layer_cov[f] = round(r3["n"] / total, 4)
            avg_cov = round(sum(layer_cov.values()) / len(fields), 4) if fields else 0.0
            layer_quality[layer_name] = {
                "fields": layer_cov,
                "avg_coverage": avg_cov,
                "n_fields": len(fields),
            }
    report["layer_quality"] = layer_quality

    # 最差 10 只
    worst = conn.execute("""
        SELECT ipo_id, stock_code, company_name_zh, data_quality_score
        FROM ipo_master
        ORDER BY data_quality_score ASC, ipo_id
        LIMIT 10
    """).fetchall()
    report["worst_ipos"] = [
        {
            "ipo_id": w["ipo_id"],
            "stock_code": w["stock_code"],
            "company_name_zh": w["company_name_zh"],
            "score": w["data_quality_score"],
        }
        for w in worst
    ]

    return report


def save_quality_report(report: Dict[str, Any],
                        output_path: Optional[Path] = None) -> Path:
    """将质量报告写入 JSON 文件.

    默认: data/data_quality_report.json
    """
    if output_path is None:
        output_path = Path(__file__).resolve().parents[2] / "data" / "data_quality_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _log.info("数据质量报告已保存: %s", output_path)
    return output_path
