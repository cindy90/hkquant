"""
Investment thesis 综合: 把 NACSResult + panel snapshot + similar_cases
转成 IC memo 顶部的"主驱动 / 主风险 / base rate"叙事段.

设计原则:
  1. 纯规则模板, 不调 LLM (可重复 + 审计友好)
  2. 阈值与 nacs_rationale 同源, 一处改两处跟进
  3. 输出结构化 dict, 模板按 bullet 渲染
  4. 即便没有 panel snapshot 也能跑 (drivers/risks 独立成段; base_rate 缺失则跳过)

公开 API:
  synthesize_thesis(result, panel_snap, similar_cases) -> Dict
"""
from __future__ import annotations

import json
import statistics
from typing import Any, Dict, List, Optional, Tuple


# =============================================================================
# 阈值: 什么算"主驱动" / "主风险"
# =============================================================================

# Q_company / Q_ecosystem 子项 raw_score (0-100): ≥75 算驱动, ≤45 算风险
SUB_DRIVER_HIGH = 75.0
SUB_DRIVER_LOW = 45.0
# R_lockup 子项是 0-1 风险值: ≥0.40 算主要风险, ≤0.15 算缓解项
R_DRIVER_HIGH_RISK = 0.40
R_DRIVER_LOW_RISK = 0.15

# 各因子整体阈值
Q_HIGH = 0.65
Q_LOW = 0.45
R_HIGH = 0.30

# 主题热度 verdict 阈值
HEAT_OVERHEATED = 80          # ≥80 → overheated (锁定期反转风险)
HEAT_TROUGH = 40              # <40 → trough (主题谷底, 基石入场好时机)
HEAT_WARM = 60                # 60-80 → warm


# =============================================================================
# Helpers
# =============================================================================

def _l1_label(key: str) -> str:
    labels = {
        "L1.1_valuation": "估值合理性",
        "L1.2_sponsor": "保荐人质量",
        "L1.3_fundamentals": "基本面质量",
        "L1.4_offering": "发行结构",
        "L1.5_chapter": "上市章节",
        "L1.6_market": "市场环境",
    }
    return labels.get(key, key)


def _l2_label(key: str) -> str:
    labels = {
        "Q_weighted": "基石加权质量",
        "coverage": "基石覆盖率",
        "hhi": "基石集中度",
        "diversity_entropy": "类型多样性",
        "affiliation_pct": "关联污染",
        "synergy_score": "产业协同",
        "zucou_red_flag": "国资凑数",
        "cluster_bonus": "簇基石",
    }
    return labels.get(key, key)


def _l3_label(key: str) -> str:
    labels = {
        "vol_risk": "波动率风险",
        "val_reversal_risk": "估值回撤风险",
        "overhang_risk": "overhang 解禁压力",
        "fundamental_risk": "基本面恶化风险",
        "macro_risk": "HSI 系统性风险",
        "peer_lockup_risk": "同行锁定期 drawdown",
    }
    return labels.get(key, key)


# =============================================================================
# Drivers / Risks 提取
# =============================================================================

def _extract_l1_drivers_risks(layer1) -> tuple:
    """从 L1 子项 raw_score (0-100) 提取 drivers (≥75) / risks (≤45)"""
    drivers, risks = [], []
    for key in ["L1.1_valuation", "L1.2_sponsor", "L1.3_fundamentals",
                "L1.4_offering", "L1.5_chapter", "L1.6_market"]:
        val = layer1.components.get(key)
        if val is None:
            continue
        reason = layer1.reasons.get(key, "")
        if val >= SUB_DRIVER_HIGH:
            drivers.append({
                "name": _l1_label(key), "score": val,
                "tier": "L1", "reason": reason,
            })
        elif val <= SUB_DRIVER_LOW:
            risks.append({
                "name": _l1_label(key), "score": val,
                "tier": "L1", "reason": reason,
            })
    return drivers, risks


def _extract_l2_drivers_risks(layer2) -> tuple:
    """从 L2 部分子项 (subset 是 0-100 score) 提取"""
    drivers, risks = [], []
    score_keys = ["coverage_score", "hhi_score", "diversity_score",
                  "pollution_score", "synergy_score"]
    label_lookup = {
        "coverage_score": "coverage",
        "hhi_score": "hhi",
        "diversity_score": "diversity_entropy",
        "pollution_score": "affiliation_pct",
        "synergy_score": "synergy_score",
    }
    for sk in score_keys:
        val = layer2.components.get(sk)
        if val is None:
            continue
        label_key = label_lookup[sk]
        reason = layer2.reasons.get(label_key, "")
        if val >= SUB_DRIVER_HIGH:
            drivers.append({
                "name": _l2_label(label_key), "score": val,
                "tier": "L2", "reason": reason,
            })
        elif val <= SUB_DRIVER_LOW:
            risks.append({
                "name": _l2_label(label_key), "score": val,
                "tier": "L2", "reason": reason,
            })
    # zucou red flag 单独处理 (它是布尔信号, 不是 0-100 score)
    if layer2.components.get("zucou_red_flag", 0) >= 1:
        risks.append({
            "name": "国资凑数红旗",
            "score": None,
            "tier": "L2",
            "reason": layer2.reasons.get("zucou_red_flag", "中资>70% 且长线<30%"),
        })
    # cluster bonus 加成
    cb = layer2.components.get("cluster_bonus", 1.0)
    if cb > 1.0:
        drivers.append({
            "name": "簇基石加成",
            "score": cb * 100,
            "tier": "L2",
            "reason": layer2.reasons.get("cluster_bonus",
                                          f"cluster_count multiplier ×{cb:.2f}"),
        })
    return drivers, risks


def _extract_l3_risks(layer3) -> List[Dict]:
    """L3 全是风险维度 (越大越糟); 提取 ≥0.40 的项"""
    risks = []
    for key in ["vol_risk", "val_reversal_risk", "overhang_risk",
                "fundamental_risk", "macro_risk", "peer_lockup_risk"]:
        val = layer3.components.get(key)
        if val is None:
            continue
        if val >= R_DRIVER_HIGH_RISK:
            risks.append({
                "name": _l3_label(key), "score": val,
                "tier": "L3", "reason": layer3.reasons.get(key, ""),
            })
    return risks


# =============================================================================
# Base rate from similar_cases
# =============================================================================

def _base_rate_from_similar(similar_cases: List[Dict]) -> Optional[Dict]:
    """从 similar_cases (含 actual_d30 / actual_m6) 算 base rate.

    返回:
        {
            "n_total": 5, "n_d30_due": 4, "n_m6_due": 3,
            "d30_median": 0.05, "d30_winrate": 0.75,
            "m6_median": 0.20, "m6_winrate": 0.67,
            "verdict": "favorable" / "neutral" / "cautious"
        }
        或 None (没有 similar_cases)
    """
    if not similar_cases:
        return None
    d30_vals = [s["actual_d30"] for s in similar_cases
                if s.get("actual_d30") is not None]
    m6_vals = [s["actual_m6"] for s in similar_cases
               if s.get("actual_m6") is not None]
    out: Dict[str, Any] = {
        "n_total": len(similar_cases),
        "n_d30_due": len(d30_vals),
        "n_m6_due": len(m6_vals),
        "d30_median": statistics.median(d30_vals) if d30_vals else None,
        "d30_winrate": (sum(1 for v in d30_vals if v > 0) / len(d30_vals)
                        if d30_vals else None),
        "m6_median": statistics.median(m6_vals) if m6_vals else None,
        "m6_winrate": (sum(1 for v in m6_vals if v > 0) / len(m6_vals)
                       if m6_vals else None),
    }
    # verdict: 用 m6 优先, 没的话用 d30
    judge = out["m6_median"] if out["m6_median"] is not None else out["d30_median"]
    if judge is None:
        out["verdict"] = "no_due_samples"
    elif judge >= 0.10:
        out["verdict"] = "favorable"
    elif judge >= -0.05:
        out["verdict"] = "neutral"
    else:
        out["verdict"] = "cautious"
    return out


# =============================================================================
# Theme heat panel — 主题情绪 (来自 themes/heat_today + history)
# =============================================================================

def _heat_verdict(heat_score: Optional[int]) -> str:
    """heat_score (0-100) → verdict label"""
    if heat_score is None:
        return "unknown"
    if heat_score >= HEAT_OVERHEATED:
        return "overheated"      # ≥80 锁定期反转风险
    if heat_score >= HEAT_WARM:
        return "warm"             # 60-79
    if heat_score >= HEAT_TROUGH:
        return "moderate"         # 40-59
    return "trough"               # <40 谷底


def _heat_warning_for_verdict(verdict: str, heat_score: Optional[int]) -> Optional[str]:
    if verdict == "overheated":
        return (f"主题热度 {heat_score}/100 ≥{HEAT_OVERHEATED} (overheated) — "
                f"锁定期反转风险高, 基石入场前评估退出 timing")
    if verdict == "trough":
        return (f"主题热度 {heat_score}/100 <{HEAT_TROUGH} (trough) — "
                f"主题谷底, 可能是基石入场好时机, 但需确认基本面非趋势性恶化")
    return None


def _build_theme_heat_section(theme_id: Optional[str],
                              themes_bundle: Optional[Dict[str, Any]]
                              ) -> Optional[Dict[str, Any]]:
    """组装 theme_heat 字段; 返回 None 时表示无法构造 (主题未识别 / 数据缺)"""
    if not theme_id or not themes_bundle:
        return None
    heat_data, heat_prov = themes_bundle.get("heat_today") or (None, None)
    if not heat_data or theme_id not in heat_data.get("themes", {}):
        return None

    rec = heat_data["themes"][theme_id]
    score = rec.get("heat_score")
    verdict = _heat_verdict(score)

    # 30d 趋势 (来自 history.csv)
    history_data, _ = themes_bundle.get("history") or (None, None)
    trend_30d: List[Tuple[str, int]] = []
    if history_data and theme_id in history_data:
        trend_30d = history_data[theme_id][-30:]

    return {
        "theme_id": theme_id,
        "label": rec.get("label", theme_id),
        "heat_score": score,
        "verdict": verdict,
        "ret_5d": rec.get("ret_5d"),
        "ret_20d": rec.get("ret_20d"),
        "ret_60d": rec.get("ret_60d"),
        "pe_ttm_avg": rec.get("pe_ttm_avg"),
        "reason": rec.get("reason"),
        "warning_from_source": rec.get("warning"),
        "warning_from_verdict": _heat_warning_for_verdict(verdict, score),
        "source": rec.get("source"),
        "trend_30d": [{"date": d, "score": s} for d, s in trend_30d],
        "asof": heat_data.get("as_of"),
    }


# =============================================================================
# Premium estimate panel — AI 镀金溢价测算
# =============================================================================

def _lookup_premium(ai_pct: float, lookup_table: List[Dict]) -> Optional[float]:
    """在 premium_curve.lookup_table 里 nearest-neighbor 查 ai_pct → premium."""
    if not lookup_table:
        return None
    # 找最接近的 ai_pct 行
    nearest = min(lookup_table,
                  key=lambda r: abs((r.get("ai_pct") or 0) - ai_pct))
    return nearest.get("premium")


def _build_premium_estimate(ai_revenue_pct: Optional[float],
                            themes_bundle: Optional[Dict[str, Any]]
                            ) -> Optional[Dict[str, Any]]:
    """组装 premium_estimate 字段; 返回 None 时表示无法构造."""
    if ai_revenue_pct is None or not themes_bundle:
        return None
    curve_data, curve_prov = themes_bundle.get("premium_curve") or (None, None)
    if not curve_data:
        return None

    expected = _lookup_premium(ai_revenue_pct, curve_data.get("lookup_table", []))
    if expected is None:
        return None

    r_squared = curve_data.get("r_squared")
    n_used = curve_data.get("n_samples_used")
    fitted_at = curve_data.get("fitted_at")

    # 解读句子 (跟 nacs_checklist_tool VIII 区一致)
    r2_str = f"{r_squared:.2f}" if r_squared is not None else "n/a"
    interp = (
        f"{ai_revenue_pct:.0%} AI 收入 → 期望溢价 {expected:+.0%} "
        f"(模型: {curve_data.get('model', 'unknown')}; "
        f"n={n_used} 样本; R²={r2_str})"
    )
    if r_squared is not None and r_squared < 0.30:
        interp += " ⚠ R² 偏低, 估计置信度有限"

    return {
        "ai_revenue_pct": ai_revenue_pct,
        "expected_premium": expected,
        "model": curve_data.get("model"),
        "params": curve_data.get("params"),
        "r_squared": r_squared,
        "n_samples_used": n_used,
        "fitted_at": fitted_at,
        "interpretation": interp,
        "is_stale": curve_prov.is_stale if curve_prov else False,
    }


def _resolve_ai_revenue_pct(stock_code: Optional[str],
                            override: Optional[float],
                            themes_bundle: Optional[Dict[str, Any]]
                            ) -> Tuple[Optional[float], str]:
    """
    返回 (ai_revenue_pct, source). 优先级:
        1. CLI/YAML override
        2. ai_revenue_manual.json[stock_code]
        3. None
    source 是字符串说明数据出处, 写入 provenance.
    """
    if override is not None:
        return float(override), f"override (analyze_deal --ai-revenue-pct or deal YAML)"
    if stock_code and themes_bundle:
        manual_data, manual_prov = themes_bundle.get("ai_revenue_manual") or (None, None)
        if manual_data:
            # 跟 themes_data._canon 一致: 去前导 0 后比较
            canon = stock_code.upper().strip()
            if "." in canon:
                num, suffix = canon.split(".", 1)
                canon = f"{num.lstrip('0') or '0'}.{suffix}"
            if canon in manual_data:
                return manual_data[canon], f"ai_revenue_manual.json[{canon}]"
    return None, "no_ai_revenue_data"


# =============================================================================
# Themes provenance bundle — 把 5 个文件的 provenance 整理成一个 dict
# =============================================================================

def _themes_provenance(themes_bundle: Optional[Dict[str, Any]],
                       theme_id: Optional[str],
                       classification_result: Optional[Any],
                       ai_revenue_source: Optional[str]) -> Dict[str, Any]:
    """
    返回写进 nacs_predictions.themes_provenance_json 的 dict, 含:
        - 5 个文件各自的 (status, mtime, asof, is_stale)
        - classification_result 的 reason + matched_signals
        - ai_revenue_pct 的来源
    """
    out: Dict[str, Any] = {
        "theme_id": theme_id,
        "ai_revenue_source": ai_revenue_source,
    }
    if themes_bundle:
        for name, value in themes_bundle.items():
            if isinstance(value, tuple) and len(value) == 2:
                _, prov = value
                if prov is not None:
                    out[name] = prov.to_dict() if hasattr(prov, "to_dict") else prov
    if classification_result is not None and hasattr(classification_result, "to_dict"):
        out["classification"] = classification_result.to_dict()
    return out


# =============================================================================
# 主入口
# =============================================================================

def synthesize_thesis(result,
                      panel_snap: Optional[Dict] = None,
                      similar_cases: Optional[List[Dict]] = None,
                      themes_bundle: Optional[Dict[str, Any]] = None,
                      stock_code: Optional[str] = None,
                      gics_l2: Optional[str] = None,
                      ipo_concept_names: Optional[List[str]] = None,
                      company_name: Optional[str] = None,
                      ai_revenue_pct_override: Optional[float] = None
                      ) -> Dict[str, Any]:
    """生成投资逻辑综合 dict.

    新增参数 (S3, 接管 themes 面板):
        themes_bundle:   reports.themes_data.load_all() 的返回; None 时不出 theme panel
        stock_code:      用于 classify_deal_to_theme + ai_revenue_manual lookup
        gics_l2 / ipo_concept_names / company_name:  classifier 的输入
        ai_revenue_pct_override:  优先于 ai_revenue_manual

    返回结构 (S3 后):
        {
            "headline", "drivers", "risks", "warnings", "base_rate",
            "panel_context",
            # 新增:
            "theme_heat": {...} | None,
            "premium_estimate": {...} | None,
            "themes_provenance": {...},          # 永远有值, 即便降级
        }
    """
    # 1. drivers / risks
    l1_d, l1_r = _extract_l1_drivers_risks(result.layer1)
    l2_d, l2_r = _extract_l2_drivers_risks(result.layer2)
    l3_r = _extract_l3_risks(result.layer3)
    drivers = l1_d + l2_d
    risks = l1_r + l2_r + l3_r

    # 2. base rate from similar_cases
    base_rate = _base_rate_from_similar(similar_cases or [])

    # 3. headline (1 sentence)
    headline = _make_headline(result, drivers, risks, base_rate)

    # 4. panel context
    panel_ctx = None
    if panel_snap:
        panel_ctx = {
            "snapshot_id": panel_snap.get("snapshot_id"),
            "asof": str(panel_snap.get("asof_date") or "")[:10],
            "n_panel": panel_snap.get("n_ipos_in_universe"),
            "regime_score": panel_snap.get("regime_score"),
        }
        regime = panel_ctx.get("regime_score")
        if regime is not None:
            if regime >= 0.05:
                panel_ctx["regime_label"] = "情绪正面 (regime≥+0.05)"
            elif regime >= 0.0:
                panel_ctx["regime_label"] = "情绪中性 (regime ≈ 0)"
            else:
                panel_ctx["regime_label"] = "情绪偏弱 (regime<0)"

    # 5. theme classification (S3 新增)
    classification = None
    theme_id = None
    if themes_bundle and stock_code:
        from reports.themes_data import classify_deal_to_theme
        defs_data, _ = themes_bundle.get("theme_definitions") or (None, None)
        classification = classify_deal_to_theme(
            stock_code=stock_code, gics_l2=gics_l2,
            ipo_concept_names=ipo_concept_names,
            company_name=company_name,
            theme_definitions=defs_data,
        )
        theme_id = classification.theme_id

    # 6. theme heat panel
    theme_heat = _build_theme_heat_section(theme_id, themes_bundle)

    # 7. premium estimate panel
    ai_pct, ai_source = _resolve_ai_revenue_pct(
        stock_code, ai_revenue_pct_override, themes_bundle,
    )
    premium_est = _build_premium_estimate(ai_pct, themes_bundle)

    # 8. themes provenance (永远有值)
    themes_prov = _themes_provenance(
        themes_bundle, theme_id, classification, ai_source,
    )

    return {
        "headline": headline,
        "drivers": drivers,
        "risks": risks,
        "warnings": list(result.warnings),
        "base_rate": base_rate,
        "panel_context": panel_ctx,
        "theme_heat": theme_heat,
        "premium_estimate": premium_est,
        "themes_provenance": themes_prov,
    }


def _make_headline(result, drivers: List[Dict], risks: List[Dict],
                   base_rate: Optional[Dict]) -> str:
    """1 句话头条."""
    decision = result.decision
    pos = result.position_pct
    nacs = result.nacs_adjusted

    if decision == "SKIP":
        # 检查是不是 regime gate
        regime_blocked = any("regime_gate" in (a or "").lower()
                              for a in result.adjustments_applied)
        if regime_blocked:
            return (f"建议 SKIP: 模型评分 NACS_adj={nacs:.4f} 本身落在 ≥{0.25} 的"
                    f"积极区间, 但 regime gate 阻断 (市场环境不适合).")
        if result.warnings:
            return f"建议 SKIP: NACS_adj={nacs:.4f}, 主因: {result.warnings[0]}"
        return f"建议 SKIP: NACS_adj={nacs:.4f} 落入 SKIP 区间 (<0.25)."

    # 非 SKIP: 总结主驱动 + 主风险
    n_drivers = len(drivers)
    n_risks = len(risks)
    drv_str = (f"{n_drivers} 项强驱动" if n_drivers
                else "无突出驱动")
    risk_str = (f"{n_risks} 项主风险" if n_risks else "无主要风险")
    base_phrase = ""
    if base_rate and base_rate.get("verdict"):
        v = base_rate["verdict"]
        phrase_map = {
            "favorable": "类比组实战正面",
            "neutral": "类比组实战中性",
            "cautious": "⚠ 类比组实战偏负",
            "no_due_samples": "类比组未到期",
        }
        base_phrase = f", {phrase_map.get(v, v)}"
    return (f"建议 {decision} ({pos:.0%}): NACS_adj={nacs:.4f}, "
            f"{drv_str} / {risk_str}{base_phrase}.")
