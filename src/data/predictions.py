"""
NACS predictions: 单 deal 评估结果的落盘 + 同伴比对 + 复盘查询.

数据流:
    NACSResult (compute_nacs 输出)
        + IPOOffering (输入快照)
        + panel_snapshot_id (上下文锁)
        + price_scenario (low/mid/high/final)
            ↓ persist_prediction()
    nacs_predictions 表 (audit trail; 不可改, 只能 append)
            ↓
    case_review.py 把多次 prediction + 实际 ipo_returns 做 diff
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


# =============================================================================
# 序列化 helper
# =============================================================================

def _to_jsonable(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if hasattr(obj, "value") and hasattr(obj, "name"):  # Enum
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def _make_case_id(stock_code: str, asof: date, scenario: str,
                  panel_snapshot_id: str) -> str:
    """e.g. PRED_1187.HK_2026-05-09_mid_a3f2c1"""
    h = hashlib.sha1(
        f"{stock_code}|{asof}|{scenario}|{panel_snapshot_id}".encode()
    ).hexdigest()[:6]
    return f"PRED_{stock_code}_{asof.isoformat()}_{scenario}_{h}"


# =============================================================================
# Percentile / similar_cases helpers
# =============================================================================

def _percentile(value: float, sorted_values: List[float]) -> Optional[float]:
    """value 在排序值中的百分位 (0..1, 0=最小, 1=最大)"""
    if not sorted_values:
        return None
    n = len(sorted_values)
    # 找 value 严格小于的索引数 / n  (rank-based)
    below = sum(1 for v in sorted_values if v < value)
    return below / n


def compute_panel_percentile(conn: sqlite3.Connection,
                             nacs_value: float,
                             panel_snapshot_id: str,
                             chapter: Optional[str] = None
                             ) -> Tuple[Optional[float], Optional[float]]:
    """返回 (overall_pct_in_panel, pct_in_chapter).

    实现方式: 从同 panel snapshot 的 nacs_predictions 中读其它 case 的 NACS 做对照
    (如果没历史 prediction, 则用 panel 成员的 ipo_returns d30 作 proxy 排序 — 退化方案).

    最干净方法是事后跑 batch backtest 把 panel 384 只都打分入 nacs_predictions,
    然后这个查询直接对比. 暂时用 d30/m6 作 proxy.
    """
    snap = conn.execute(
        "SELECT member_ipo_ids_json FROM panel_snapshots WHERE snapshot_id=?",
        (panel_snapshot_id,)
    ).fetchone()
    if not snap:
        return None, None

    # 同 panel 中所有 listed IPO 的 NACS 不可知 (回测时未必每只都跑过 prediction)
    # 退化: 用 listed IPO 的 return_d30 做"市场打分"代理, 把 nacs_value 映射进同分位
    member_ids = json.loads(snap["member_ipo_ids_json"])
    if not member_ids:
        return None, None

    placeholders = ",".join("?" for _ in member_ids)
    rows = conn.execute(
        f"SELECT m.listing_chapter, r.return_d30 "
        f"FROM ipo_master m LEFT JOIN ipo_returns r ON m.ipo_id = r.ipo_id "
        f"WHERE m.ipo_id IN ({placeholders}) AND r.is_d30_due=1",
        member_ids,
    ).fetchall()
    overall_d30 = sorted(r["return_d30"] for r in rows
                         if r["return_d30"] is not None)
    chapter_d30 = sorted(r["return_d30"] for r in rows
                         if r["listing_chapter"] == chapter
                         and r["return_d30"] is not None) if chapter else []

    # 这是 fallback: 用 NACS 投影到 d30 量纲不准确, 留空让后续真正打分时填
    # (这个函数本质上需要"全 panel 都跑过 prediction"才能有意义,
    #  现阶段只先返回结构, 暂用 d30 量纲做近似排名)
    return _percentile(nacs_value, overall_d30) if overall_d30 else None, \
           _percentile(nacs_value, chapter_d30) if chapter_d30 else None


def find_similar_cases(conn: sqlite3.Connection, *,
                       chapter: Optional[str],
                       gics_l2: Optional[str],
                       q_company: float,
                       q_ecosystem: float,
                       r_lockup: float,
                       k: int = 5,
                       min_listing_date: Optional[str] = None
                       ) -> List[Dict[str, Any]]:
    """从 panel (status='listed') 中找 k 个最相似的历史 IPO.

    匹配规则:
      hard: 同 chapter 优先 (no chapter match 则放宽到 same gics_l2)
      soft rank: 几何距离 √((Q_c_diff)² + (Q_e_diff)² + 0.5×(R_l_diff)²)
                 但当前 panel 的 IPO 的 NACS 子项不一定都跑过, 用代理:
                   - 同章节同行业 → 距离 0
                   - 仅同章节     → 距离 0.5
                   - 仅同行业     → 距离 0.7
                   - 都不同       → 0.9
                 然后用 listing_date 越近越优先.
    """
    cutoff = min_listing_date or "2022-01-01"

    sql = """
        SELECT m.ipo_id, m.stock_code, m.company_name_zh,
               m.listing_date, m.listing_chapter, m.gics_l2,
               r.return_d30, r.return_m6, r.return_m12,
               r.is_d30_due, r.is_m6_due
        FROM ipo_master m
        LEFT JOIN ipo_returns r ON r.ipo_id = m.ipo_id
        WHERE m.status = 'listed'
          AND m.listing_date >= ?
        ORDER BY m.listing_date DESC
    """
    rows = conn.execute(sql, (cutoff,)).fetchall()

    scored = []
    for row in rows:
        same_chapter = (chapter is not None and
                        row["listing_chapter"] == chapter)
        same_gics = (gics_l2 is not None and
                     row["gics_l2"] == gics_l2)
        if same_chapter and same_gics:
            base = 0.0
            match_dims = ["chapter", "gics_l2"]
        elif same_chapter:
            base = 0.5
            match_dims = ["chapter"]
        elif same_gics:
            base = 0.7
            match_dims = ["gics_l2"]
        else:
            continue  # 完全不match 的不要进 similar_cases (噪声)
        scored.append({
            "ipo_id": row["ipo_id"],
            "stock_code": row["stock_code"],
            "name": row["company_name_zh"],
            "listing_date": str(row["listing_date"])[:10] if row["listing_date"] else None,
            "match_dims": match_dims,
            "similarity_score": 1.0 - base,
            "actual_d30": row["return_d30"] if row["is_d30_due"] == 1 else None,
            "actual_m6": row["return_m6"] if row["is_m6_due"] == 1 else None,
        })

    # 排序: 相似度高 + listing_date 近
    scored.sort(key=lambda x: (-x["similarity_score"],
                               x["listing_date"] or "0000-00-00"),
                reverse=False)
    return scored[:k]


# =============================================================================
# persist
# =============================================================================

def persist_prediction(conn: sqlite3.Connection,
                       *,
                       result,                              # NACSResult
                       offering,                            # IPOOffering
                       stock_code: str,
                       asof: date,
                       panel_snapshot_id: str,
                       deal_status_at_analysis: Optional[str] = None,
                       price_scenario: str = "mid",
                       offer_price_used: Optional[float] = None,
                       notes: Optional[str] = None,
                       thesis: Optional[Dict[str, Any]] = None) -> str:
    """落一行 nacs_predictions; 返回 case_id.

    幂等: 同 (stock_code, asof, scenario, panel_snapshot_id) 重复跑会 update
          (避免 audit trail 噪音; 真要"再分析一次"应换 panel 或换 asof).

    thesis (S3 起新增): synthesize_thesis() 返回的 dict; 含 theme_heat /
        premium_estimate / themes_provenance. 持久化以下衍生:
            - theme_id, theme_confidence, theme_heat_score
            - ai_revenue_pct_used  (premium_estimate.ai_revenue_pct)
            - themes_provenance_json  (full audit blob)
    """
    case_id = _make_case_id(stock_code, asof, price_scenario, panel_snapshot_id)

    # NACSResult 内嵌 LayerBreakdown; 取 components dict
    l1 = getattr(result.layer1, "components", {}) or {}
    l2 = getattr(result.layer2, "components", {}) or {}
    l3 = getattr(result.layer3, "components", {}) or {}

    # similar_cases (基于 IPOOffering 的 chapter / gics_l2 / Q 值)
    chapter_val = (offering.listing_chapter.value
                   if hasattr(offering.listing_chapter, "value")
                   else offering.listing_chapter)
    gics_val = getattr(offering, "gics_l2", None)
    sim = find_similar_cases(
        conn,
        chapter=chapter_val,
        gics_l2=gics_val,
        q_company=result.Q_company,
        q_ecosystem=result.Q_ecosystem,
        r_lockup=result.R_lockup,
        min_listing_date=(asof.replace(year=asof.year - 2)).isoformat(),
    )

    pct_panel, pct_chapter = compute_panel_percentile(
        conn, result.nacs_adjusted, panel_snapshot_id, chapter=chapter_val
    )

    inputs_json = json.dumps(_to_jsonable(offering), ensure_ascii=False)

    # S3 新增: theme audit 字段从 thesis dict 提取
    theme_id_val: Optional[str] = None
    theme_confidence_val: Optional[str] = None
    theme_heat_score_val: Optional[int] = None
    ai_revenue_pct_used_val: Optional[float] = None
    themes_provenance_json_val: Optional[str] = None
    if thesis:
        if thesis.get("theme_heat"):
            theme_id_val = thesis["theme_heat"].get("theme_id")
            theme_heat_score_val = thesis["theme_heat"].get("heat_score")
        if thesis.get("premium_estimate"):
            ai_revenue_pct_used_val = thesis["premium_estimate"].get("ai_revenue_pct")
        prov = thesis.get("themes_provenance") or {}
        classification = prov.get("classification") or {}
        theme_confidence_val = classification.get("confidence")
        if not theme_id_val:
            theme_id_val = prov.get("theme_id")
        themes_provenance_json_val = json.dumps(
            _to_jsonable(prov), ensure_ascii=False
        )

    conn.execute("""
        INSERT INTO nacs_predictions (
            case_id, stock_code, asof_date, panel_snapshot_id,
            deal_status_at_analysis, price_scenario, offer_price_used,
            nacs_raw, nacs_adjusted, Q_company, Q_ecosystem, R_lockup,
            decision, position_pct, cluster_count,
            layer1_components_json, layer2_components_json, layer3_components_json,
            adjustments_json, warnings_json, inputs_json,
            nacs_pct_in_panel, nacs_pct_in_chapter, similar_cases_json, notes,
            theme_id, theme_confidence, theme_heat_score,
            ai_revenue_pct_used, themes_provenance_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?)
        ON CONFLICT(case_id) DO UPDATE SET
            nacs_raw = excluded.nacs_raw,
            nacs_adjusted = excluded.nacs_adjusted,
            Q_company = excluded.Q_company,
            Q_ecosystem = excluded.Q_ecosystem,
            R_lockup = excluded.R_lockup,
            decision = excluded.decision,
            position_pct = excluded.position_pct,
            cluster_count = excluded.cluster_count,
            layer1_components_json = excluded.layer1_components_json,
            layer2_components_json = excluded.layer2_components_json,
            layer3_components_json = excluded.layer3_components_json,
            adjustments_json = excluded.adjustments_json,
            warnings_json = excluded.warnings_json,
            inputs_json = excluded.inputs_json,
            theme_id = excluded.theme_id,
            theme_confidence = excluded.theme_confidence,
            theme_heat_score = excluded.theme_heat_score,
            ai_revenue_pct_used = excluded.ai_revenue_pct_used,
            themes_provenance_json = excluded.themes_provenance_json,
            nacs_pct_in_panel = excluded.nacs_pct_in_panel,
            nacs_pct_in_chapter = excluded.nacs_pct_in_chapter,
            similar_cases_json = excluded.similar_cases_json,
            notes = excluded.notes,
            run_at = CURRENT_TIMESTAMP
    """, (
        case_id, stock_code, asof.isoformat(), panel_snapshot_id,
        deal_status_at_analysis, price_scenario, offer_price_used,
        result.nacs_raw, result.nacs_adjusted,
        result.Q_company, result.Q_ecosystem, result.R_lockup,
        result.decision, result.position_pct,
        getattr(offering, "cluster_cornerstone_count", 0),
        json.dumps(_to_jsonable(l1), ensure_ascii=False),
        json.dumps(_to_jsonable(l2), ensure_ascii=False),
        json.dumps(_to_jsonable(l3), ensure_ascii=False),
        json.dumps(list(getattr(result, "adjustments_applied", [])),
                   ensure_ascii=False),
        json.dumps(list(getattr(result, "warnings", [])), ensure_ascii=False),
        inputs_json,
        pct_panel, pct_chapter,
        json.dumps(_to_jsonable(sim), ensure_ascii=False),
        notes,
        # S3 新增 5 列:
        theme_id_val, theme_confidence_val, theme_heat_score_val,
        ai_revenue_pct_used_val, themes_provenance_json_val,
    ))
    return case_id


# =============================================================================
# Lookups
# =============================================================================

def list_predictions_for_stock(conn: sqlite3.Connection,
                               stock_code: str) -> List[Dict[str, Any]]:
    """按 asof_date 升序返回该 stock_code 的所有预测."""
    rows = conn.execute(
        "SELECT * FROM nacs_predictions WHERE stock_code = ? "
        "ORDER BY asof_date, run_at",
        (stock_code,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_prediction(conn: sqlite3.Connection, case_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM nacs_predictions WHERE case_id = ?", (case_id,)
    ).fetchone()
    return dict(row) if row else None
