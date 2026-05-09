"""
panel_snapshots: 把全量 listed IPO 面板的可还原状态冻起来.

每次 run_v7_backtest 跑完会写一行 panel_snapshots; 单 deal 评估 (analyze_deal)
引用最近的 snapshot_id 锁定上下文 (pe_peer_median, regime_score, MarketEnvironment).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional


# =============================================================================
# 元数据采集 helpers
# =============================================================================

def _git_sha(cwd: Optional[Path] = None) -> Optional[str]:
    """获取当前 HEAD git sha; 失败返回 None (不阻塞)."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError):
        return None


def _config_hash(cfg_dict: Dict[str, Any]) -> str:
    """规范化 cfg dict → sha1[:12]"""
    canonical = json.dumps(cfg_dict, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode()).hexdigest()[:12]


def _make_snapshot_id(asof: date, cfg_hash: str) -> str:
    """e.g. PANEL_2026-05-09_a3f2c1"""
    return f"PANEL_{asof.isoformat()}_{cfg_hash[:6]}"


def _serialize_for_json(obj: Any) -> Any:
    """递归把 dataclass/Enum/date 转成可 JSON 化的 primitive."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return _serialize_for_json(asdict(obj))
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_for_json(x) for x in obj]
    if hasattr(obj, "value") and hasattr(obj, "name"):  # Enum
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


# =============================================================================
# Panel aggregates 计算 (从 listed-only 子集)
# =============================================================================

def compute_panel_aggregates(
    conn,
    theme_definitions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """从 mv_ipo_full WHERE status='listed' 算跨章节 / 跨年的中位/IQR.

    返回结构:
        {
          "by_chapter": {
             "main_board_profitable": {
                "n": 269,
                "pe_at_offer_median": 18.0, "pe_at_offer_p25": 12.0, "pe_at_offer_p75": 28.0,
                "return_d30_median": 0.05, "return_d30_p25": -0.05, "return_d30_p75": 0.20,
                "return_m6_median": 0.10  (同上, 受 is_m6_due 过滤),
                ...
             },
             "18a": {...},
             ...
          },
          "by_gics_l2": { "医疗保健业(HS)-...": {...} },
          "by_theme": { "ai_server": {n, ...}, "innovative_drug": {...} },   # P2.2
          "overall": { ... 整个 listed panel 的中位/IQR ... }
        }

    P2.2: theme_definitions 传入时, 每条 listed IPO 走 classify_deal_to_theme
    生成 theme_id, 多打一个 by_theme 桶. theme_definitions=None 时跳过分桶
    (旧行为, 向后兼容).
    """
    rows = conn.execute("""
        SELECT ipo_id, stock_code, company_name_zh,
               listing_chapter, gics_l2,
               pe_at_offer, return_d30, return_m6,
               is_d30_due, is_m6_due
        FROM mv_ipo_full
        WHERE status = 'listed'
    """).fetchall()

    def _percentiles(values: List[float], pcts=(0.25, 0.50, 0.75)) -> Dict[str, float]:
        v = sorted(x for x in values if x is not None)
        if not v:
            return {f"p{int(p * 100)}": None for p in pcts}
        out = {}
        for p in pcts:
            i = int(round(p * (len(v) - 1)))
            out[f"p{int(p * 100)}"] = v[i]
        return out

    def _summarize(rs):
        pe_vals = [r["pe_at_offer"] for r in rs if r["pe_at_offer"] is not None]
        d30_vals = [r["return_d30"] for r in rs
                    if r["is_d30_due"] == 1 and r["return_d30"] is not None]
        m6_vals = [r["return_m6"] for r in rs
                   if r["is_m6_due"] == 1 and r["return_m6"] is not None]
        out = {"n": len(rs)}
        out.update({"pe_at_offer_" + k: v for k, v in _percentiles(pe_vals).items()})
        out.update({"return_d30_" + k: v for k, v in _percentiles(d30_vals).items()})
        out.update({"return_m6_" + k: v for k, v in _percentiles(m6_vals).items()})
        return out

    overall = _summarize(rows)

    by_chapter: Dict[str, Any] = {}
    by_chapter_buckets: Dict[str, list] = {}
    for r in rows:
        by_chapter_buckets.setdefault(r["listing_chapter"] or "unknown", []).append(r)
    for k, rs in by_chapter_buckets.items():
        by_chapter[k] = _summarize(rs)

    by_gics: Dict[str, Any] = {}
    by_gics_buckets: Dict[str, list] = {}
    for r in rows:
        g = r["gics_l2"]
        if not g:
            continue
        by_gics_buckets.setdefault(g, []).append(r)
    # 只保留样本 ≥ 5 的 GICS L2 (太小子样本中位数没意义)
    for k, rs in by_gics_buckets.items():
        if len(rs) >= 5:
            by_gics[k] = _summarize(rs)

    # P2.2: by_theme 桶 (theme_definitions 提供时启用)
    by_theme: Dict[str, Any] = {}
    if theme_definitions and "themes" in theme_definitions:
        from reports.themes_data import classify_deal_to_theme
        # 预取每行的 ipo_concept_names (一次 query 比逐行慢得多)
        concept_rows = conn.execute(
            "SELECT ipo_id, concept_name FROM ipo_concepts"
        ).fetchall()
        concepts_by_ipo: Dict[int, List[str]] = {}
        for cr in concept_rows:
            concepts_by_ipo.setdefault(cr["ipo_id"], []).append(cr["concept_name"])

        by_theme_buckets: Dict[str, list] = {}
        for r in rows:
            res = classify_deal_to_theme(
                stock_code=r["stock_code"] or "",
                gics_l2=r["gics_l2"],
                ipo_concept_names=concepts_by_ipo.get(r["ipo_id"]),
                company_name=r["company_name_zh"],
                theme_definitions=theme_definitions,
            )
            if res.theme_id is None:
                continue
            by_theme_buckets.setdefault(res.theme_id, []).append(r)
        # 同 GICS L2: 样本 ≥ 5 才保留
        for k, rs in by_theme_buckets.items():
            if len(rs) >= 5:
                by_theme[k] = _summarize(rs)

    return {
        "overall": overall,
        "by_chapter": by_chapter,
        "by_gics_l2": by_gics,
        "by_theme": by_theme,
    }


# =============================================================================
# Snapshot 写入
# =============================================================================

def write_panel_snapshot(conn,
                         *,
                         asof: date,
                         market_env,                    # MarketEnvironment dataclass or dict
                         regime_score: Optional[float],
                         config_dict: Dict[str, Any],
                         config_yaml_text: Optional[str] = None,
                         notes: Optional[str] = None,
                         project_root: Optional[Path] = None,
                         theme_definitions: Optional[Dict[str, Any]] = None) -> str:
    """写一行 panel_snapshots 表; 返回 snapshot_id.

    内部:
      - member_ipo_ids 来自 mv_ipo_full WHERE status='listed' (顺带保 panel 边界)
      - aggregates_json 来自 compute_panel_aggregates(conn) — theme_definitions
        提供时增加 by_theme 桶 (P2.2)
      - 元数据 (git_sha, schema_version, config_hash) 自动采集
    """
    member_rows = conn.execute(
        "SELECT ipo_id FROM ipo_master WHERE status='listed' "
        "ORDER BY listing_date"
    ).fetchall()
    member_ids = [r[0] for r in member_rows]

    aggregates = compute_panel_aggregates(conn, theme_definitions=theme_definitions)

    cfg_hash = _config_hash(config_dict)
    snapshot_id = _make_snapshot_id(asof, cfg_hash)

    schema_ver_row = conn.execute(
        "SELECT value FROM db_metadata WHERE key='schema_version'"
    ).fetchone()
    schema_ver = schema_ver_row[0] if schema_ver_row else None

    cfg_version = config_dict.get("version") if isinstance(config_dict, dict) else None

    market_env_dict = _serialize_for_json(market_env) \
        if not isinstance(market_env, dict) else market_env

    conn.execute("""
        INSERT INTO panel_snapshots (
            snapshot_id, asof_date, n_ipos_in_universe,
            market_env_json, regime_score,
            member_ipo_ids_json, aggregates_json,
            config_version, config_hash, config_yaml_snapshot,
            code_git_sha, db_schema_version, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_id) DO UPDATE SET
            asof_date = excluded.asof_date,
            n_ipos_in_universe = excluded.n_ipos_in_universe,
            market_env_json = excluded.market_env_json,
            regime_score = excluded.regime_score,
            member_ipo_ids_json = excluded.member_ipo_ids_json,
            aggregates_json = excluded.aggregates_json,
            config_yaml_snapshot = excluded.config_yaml_snapshot,
            notes = excluded.notes
    """, (
        snapshot_id,
        asof.isoformat(),
        len(member_ids),
        json.dumps(market_env_dict, ensure_ascii=False),
        regime_score,
        json.dumps(member_ids, ensure_ascii=False),
        json.dumps(aggregates, ensure_ascii=False),
        cfg_version,
        cfg_hash,
        config_yaml_text,
        _git_sha(project_root),
        schema_ver,
        notes,
    ))
    return snapshot_id


# =============================================================================
# Snapshot 读取
# =============================================================================

def get_latest_panel_snapshot(conn) -> Optional[Dict[str, Any]]:
    """最近一次 panel snapshot; 没有则 None."""
    row = conn.execute(
        "SELECT * FROM panel_snapshots ORDER BY asof_date DESC, created_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def get_panel_snapshot(conn, snapshot_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM panel_snapshots WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchone()
    return dict(row) if row else None
