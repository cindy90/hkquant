"""
NACS 评分的"为什么这么打分"解释器 — IC memo 用.

设计原则:
  1. 每个 _score_l*_* 函数对应一个 explain_* 函数, 用同样的输入再走一遍逻辑.
  2. 阈值表用模块级常量, 与 nacs_model 内同源 (改阈值时两边一起改).
  3. 输出固定格式 "<事实>; 命中 <区间> → <分数>", 模板可直接渲染.
  4. 即便分数是中性默认 (e.g. last_round_premium=None → 60.0), 也明确说明
     "数据缺失, 走中性默认".

公开 API:
  explain_layer1_components(o, components) -> Dict[str, str]
  explain_layer2_components(o, components) -> Dict[str, str]
  explain_layer3_components(o, components) -> Dict[str, str]
  explain_l1_veto(reason) -> str
  explain_l2_veto(reason) -> str
  explain_adjustment(adj_str) -> str
  explain_decision_band(nacs_adjusted, position_pct, decision) -> List[str]
  explain_formula(Q_c, Q_e, R_l, nacs_raw, nacs_adjusted, adjustments) -> List[str]
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from nacs_model import (
    BiotechFundamentals, CompanyType, CornerstoneInvestor, CornerstoneType,
    IPOOffering, ListingChapter, MarketEnvironment, OfferingStructure,
    ProfitableFundamentals, SponsorInfo, SponsorTier, TechC18Fundamentals,
    LockupContext,
    CHAPTER_BASE_SCORE, TYPE_PRIOR_SCORE,
)


# =============================================================================
# 阈值表 (与 nacs_model 内的硬编码同源; 改阈值时两边一起改)
# =============================================================================

PE_DISCOUNT_THRESHOLDS = [
    (0.30, "≥30% 大幅折让", 100.0),
    (0.00, "持平至 30% 折让", "50→100 线性"),
    (-0.20, "0~20% 溢价", "0→50 线性"),
    (-1.00, "≥20% 溢价", 0.0),
]

LAST_ROUND_THRESHOLDS = [
    (-0.10, "≤-10% 折价", 100.0),
    (0.00, "0~10% 折价", "60→100 线性"),
    (0.30, "0~30% 溢价", "60→30 线性"),
    (0.50, "30~50% 溢价", "30→0 线性"),
    (1.00, "≥50% 溢价", 0.0),
]

SPONSOR_TIER_LABELS = {
    SponsorTier.TIER_1: "Tier 1 (中金/MS/GS/UBS/JPM/华泰/CICC)",
    SponsorTier.TIER_2: "Tier 2 (海通国际/招银国际/农银国际/建银国际等)",
    SponsorTier.TIER_3: "Tier 3 (其他)",
}

CORNERSTONE_TYPE_LABELS = {
    CornerstoneType.SOVEREIGN_PENSION: "主权/养老金 (顶级长线)",
    CornerstoneType.GLOBAL_LONG_ONLY: "全球长线 multi-asset",
    CornerstoneType.TOP_HEDGE_PREIPO: "顶级对冲基金 / pre-IPO",
    CornerstoneType.CHINESE_MUTUAL_INSURANCE: "中资公募 / 保险",
    CornerstoneType.STRATEGIC_INDUSTRIAL: "产业资本 (战略协同)",
    CornerstoneType.POLICY_FUND: "政策性基金 (国资/地方)",
    CornerstoneType.PE_VC_CONTINUATION: "PE/VC continuation 退出",
    CornerstoneType.FAMILY_OFFICE_SPV: "家族办公室 / SPV (兜底)",
}


# =============================================================================
# Layer 1 explanations
# =============================================================================

def _explain_pe_discount(o: OfferingStructure) -> str:
    pe = o.pe_at_offer
    peer = o.pe_peer_median
    if pe is None or peer is None or peer <= 0:
        return "PE 数据缺失 (offer 或 peer 为 None) → 中性默认 50"
    discount = (peer - pe) / peer
    if discount >= 0.30:
        band = f"≥30% 折让 → 满分 100"
    elif discount >= 0.0:
        band = f"持平至 30% 折让 → 50→100 线性插值"
    elif discount >= -0.20:
        band = f"0~20% 溢价 → 0→50 线性插值"
    else:
        band = f"≥20% 溢价 → 0 分"
    return (f"PE_at_offer={pe:.1f} vs peer_median={peer:.1f} "
            f"→ 折让 {discount:+.1%}; 命中 [{band}]")


def _explain_last_round_premium(o: OfferingStructure) -> str:
    p = o.last_round_premium
    if p is None:
        return ("last_round_premium 数据未灌 (全表 NULL) → 中性默认 60 "
                "(待补 wind/F&S 数据后该项自动启用)")
    if p <= -0.10:
        band = "≤-10% 大幅折价 → 100"
    elif p <= 0.0:
        band = "0~10% 折价 → 60→100 线性"
    elif p <= 0.30:
        band = "0~30% 溢价 → 60→30 线性"
    elif p <= 0.50:
        band = "30~50% 溢价 → 30→0 线性"
    else:
        band = "≥50% 溢价 → 0"
    return f"last_round_premium = {p:+.1%}; 命中 [{band}]"


def _explain_l1_1(o: IPOOffering) -> str:
    """估值合理性 (L1.1) — 18C 单走 last_round_premium; 其它 = 0.6 PE + 0.4 last_round"""
    if o.company_type == CompanyType.TECH_18C:
        return ("18C 章节 PE/PS 不可比 → 单 last_round_premium 项. "
                + _explain_last_round_premium(o.offering))
    return ("L1.1 = 0.6 × PE_score + 0.4 × last_round_premium_score. "
            + _explain_pe_discount(o.offering)
            + ". " + _explain_last_round_premium(o.offering))


def _explain_l1_2(s: SponsorInfo) -> str:
    """保荐人质量 (L1.2)"""
    tier_label = SPONSOR_TIER_LABELS.get(s.primary_tier, str(s.primary_tier))
    bonus_str = ""
    if s.joint_sponsor_count == 1 and s.primary_tier == SponsorTier.TIER_1:
        bonus_str = "; 单一保荐+Tier1 → +5 加成"
    elif s.joint_sponsor_count >= 2 and s.primary_tier == SponsorTier.TIER_1:
        bonus_str = "; 联席+Tier1 → +2 加成"
    elif s.joint_sponsor_count == 1 and s.primary_tier == SponsorTier.TIER_3:
        bonus_str = "; 单一保荐+Tier3 → -5 扣分"
    pct_data = (
        s.sponsor_d30_winrate_pct_rank is not None
        or s.sponsor_breakage_rate_pct_rank is not None
        or s.sponsor_avg_d30_pct_rank is not None
    )
    src = "百分位排名" if pct_data else f"由 Tier 推断 ({tier_label} → ~85/55/30)"
    return (f"primary={s.primary_sponsor or '(unknown)'} | {tier_label} | "
            f"联席数={s.joint_sponsor_count}; 数据源={src}{bonus_str}")


def _explain_l1_3_profitable(
    f: ProfitableFundamentals,
    components: Optional[Dict[str, Any]] = None,
) -> str:
    """主板已盈利基本面 (L1.3) — 5 项加和, 各封顶; P1.2 后附 tier multiplier"""
    rev = f.revenue_cagr_3y or 0.0
    gm = f.gross_margin_trend or 0.0
    roe = f.roe_avg_3y or 0.0
    nd = f.net_debt_to_ebitda or 0.0
    fcf = f.fcf_positive_years
    base = (f"营收 CAGR_3y {rev:+.1%} (满 30% 封顶, 占 20 分) · "
            f"毛利率趋势 {gm:+.1%} ({'正→15分' if gm > 0 else '非正→4.5分'}, 占 15) · "
            f"ROE 3y 均值 {roe:.1%} (满 20% 封顶, 占 25) · "
            f"net_debt/EBITDA {nd:.1f}× (反向, 0=满分, 4=0分, 占 20) · "
            f"FCF 正年数 {fcf}/3 (占 20)")
    # P1.2: tier multiplier — components 在通过 score_layer1_company 时带 _ 前缀
    if components:
        tier = components.get("_profit_tier",
                              components.get("profit_tier", "moderate"))
        mult = components.get("_profit_tier_multiplier",
                              components.get("profit_tier_multiplier", 1.0))
        if mult != 1.0:
            tier_label = {
                "persistent": "持续盈利 (ROE≥15% AND FCF≥3y)",
                "fresh": "刚转盈 (FCF≤1y)",
            }.get(tier, tier)
            base += f" · 盈利质量 tier={tier_label} → multiplier ×{mult:.2f}"
    return base


def _explain_l1_3_biotech(
    b: BiotechFundamentals,
    components: Optional[Dict[str, Any]] = None,
) -> str:
    """18A 生物医药 (L1.3); P1.3 后附 subdomain multiplier"""
    phase_label = {"Pre": "Pre-临床", "I": "I期", "II": "II期",
                   "III": "III期", "Approved": "已获批"}.get(
        b.core_pipeline_phase, b.core_pipeline_phase)
    base = (f"核心管线 {phase_label} (Pre→10/I→30/II→60/III→85/批准→100, 占 35) · "
            f"II期+ 管线数 {b.pipeline_count_phase2plus} (满 3 封顶, 占 20) · "
            f"现金跑道 {b.cash_runway_months or 0:.0f} 月 "
            f"(满 24 封顶, 占 25) · BD 交易 2y {b.bd_deals_count_2y} 项 "
            f"(满 2 封顶, 占 20)")
    # P1.3: 子领域 multiplier
    if components:
        sub = components.get("_subdomain",
                             components.get("subdomain", "unknown"))
        mult = components.get("_subdomain_multiplier",
                              components.get("subdomain_multiplier", 1.0))
        if mult != 1.0:
            sub_zh = {
                "innovative_drug": "创新药",
                "medical_device": "医疗器械",
                "cell_gene": "细胞基因",
                "diagnostics": "诊断/IVD",
            }.get(sub, sub)
            base += f" · 18A 子领域 {sub_zh} → multiplier ×{mult:.2f}"
    return base


def _explain_l1_3_18c(t: TechC18Fundamentals) -> str:
    """18C 特专科技 (L1.3); 未商业化档强制上限 60"""
    if t.is_commercial:
        return (f"已商业化档: 营收增速 {t.revenue_growth_yoy or 0:+.1%} "
                f"(满 50% 封顶, 占 30) · "
                f"里程碑分 {t.milestone_score:.1f}/5 (占 25) · "
                f"R&D 强度 {t.rd_intensity or 0:.1%} (满 20% 封顶, 占 25) · "
                f"基础分 20")
    return (f"未商业化档 (precommercial, 强制上限 60): "
            f"里程碑分 {t.milestone_score:.1f}/5 (占 40) · "
            f"runway 默认估值 20")


def _explain_l1_3(
    o: IPOOffering,
    components: Optional[Dict[str, Any]] = None,
) -> str:
    if o.company_type == CompanyType.PROFITABLE:
        return "[主板已盈利] " + (
            _explain_l1_3_profitable(o.profitable, components) if o.profitable
            else "ProfitableFundamentals 缺失")
    if o.company_type == CompanyType.BIOTECH_18A:
        return "[18A] " + (
            _explain_l1_3_biotech(o.biotech, components) if o.biotech
            else "BiotechFundamentals 缺失")
    return "[18C] " + (
        _explain_l1_3_18c(o.tech18c) if o.tech18c else "TechC18Fundamentals 缺失")


def _explain_l1_4(o: OfferingStructure,
                  components: Optional[Dict[str, Any]] = None) -> str:
    """发行结构 (L1.4); P1.1 后 components 含 mkt_cap_modifier 时附加说明"""
    pir = o.pricing_in_range
    if 0.5 <= pir <= 0.8:
        pir_band = "区间中位偏上 (0.5~0.8) 满分 (占 20)"
    elif pir > 0.8:
        pir_band = "区间上限附近 80~100 (占 20)"
    else:
        pir_band = f"区间下半 ({pir:.1f}) → 40~100 线性 (占 20)"
    intl = o.intl_oversubscription
    intl_warn = " · ⚠ 国际配售<1.5x 红旗 → L1.4 上限压到 30" if intl < 1.5 else ""
    base = (f"区间利用率 {pir:.2f} → {pir_band} · "
            f"国际配售 {intl:.1f}x (log scale, 20x 封顶, 占 30){intl_warn} · "
            f"公开认购 {o.public_oversubscription:.0f}x (log scale, 100x 封顶, 占 15) · "
            f"clawback {'已触发(+5)' if o.clawback_triggered else '未触发(+2.5)'} · "
            f"绿鞋 {o.greenshoe_pct:.0%} (15% 最佳的 U 型, 占 15) · "
            f"募资规模 {o.offering_size_hkd / 1e9:.1f}B HKD "
            f"(1.5~8B U 型最佳, 占 15)")
    # P1.1: 总市值 modifier
    if components:
        mc_mod = components.get("_mkt_cap_modifier",
                                components.get("mkt_cap_modifier", 0.0))
        mc_val = components.get("_mkt_cap_at_offer_hkd",
                                components.get("mkt_cap_at_offer_hkd", 0.0))
        mc_bucket = components.get("_mkt_cap_bucket",
                                   components.get("mkt_cap_bucket", "n/a"))
        if mc_mod != 0.0 and mc_val:
            base += (f" · 总市值 {mc_val / 1e9:.1f}B HKD ({mc_bucket}) "
                     f"→ modifier {mc_mod:+.0f}")
    return base


def _explain_l1_5(o: IPOOffering) -> str:
    """章节路径 (L1.5)"""
    base = CHAPTER_BASE_SCORE.get(o.listing_chapter, 50)
    parts = [f"章节 {o.listing_chapter.value} → 基础分 {base}"]
    if o.is_stock_connect_eligible_expected:
        parts.append("通车预期 +10")
    else:
        parts.append("无通车预期 -5")
    if o.weighted_voting_rights:
        parts.append("WVR 加权投票 -5")
    return " | ".join(parts)


def _explain_l1_6(m: MarketEnvironment,
                  components: Optional[Dict[str, Any]] = None) -> str:
    """市场环境 (L1.6); components 含 theme_heat_modifier 时附加说明 (P0.1)"""
    base = (f"HSI 60d 收益 {m.hsi_60d_return:+.1%} (tanh, 占 20) · "
            f"波动率分位 {m.hsi_60d_vol_pct_rank:.0%} 反向 (占 20) · "
            f"近 30d IPO 平均 d30 {m.hk_ipo_30d_avg_d30:+.1%} (tanh, 占 25) · "
            f"破发率 {m.hk_ipo_30d_breakage_rate:.0%} 反向 (占 20) · "
            f"南向资金 {m.southbound_30d_net_normalized:+.2f} (tanh, 占 15)")
    # P0.1: 主题情绪 modifier 附加说明
    # score_layer1_company 给 sub-components 加前缀 '_', 这里两种 key 都查
    if components:
        mod = (components.get("_theme_heat_modifier",
                              components.get("theme_heat_modifier", 0.0)))
        score = (components.get("_theme_heat_score",
                                components.get("theme_heat_score", 0.0)))
        if mod != 0.0:
            verdict = "overheated" if score >= 80 else "trough"
            base += (f" · 主题热度 {int(score)}/100 ({verdict}) "
                     f"→ modifier {mod:+.1f}")
    return base


def explain_layer1_components(o: IPOOffering,
                              components: Dict[str, Any]) -> Dict[str, str]:
    """对 layer1.components 的每个 'L1.x_*' 公开子项, 给出 reason 字符串."""
    reasons = {}
    if "L1.1_valuation" in components:
        reasons["L1.1_valuation"] = _explain_l1_1(o)
    if "L1.2_sponsor" in components:
        reasons["L1.2_sponsor"] = _explain_l1_2(o.sponsor)
    if "L1.3_fundamentals" in components:
        reasons["L1.3_fundamentals"] = _explain_l1_3(o, components)
    if "L1.4_offering" in components:
        reasons["L1.4_offering"] = _explain_l1_4(o.offering, components)
    if "L1.5_chapter" in components:
        reasons["L1.5_chapter"] = _explain_l1_5(o)
    if "L1.6_market" in components:
        reasons["L1.6_market"] = _explain_l1_6(o.market, components)
    return reasons


def explain_l1_veto(reason: Optional[str]) -> Optional[str]:
    """L1 veto 触发条件解释; reason 是 _check_l1_veto 返回的简短字符串"""
    if not reason:
        return None
    explanations = {
        "国际配售认购": "国际配售认购倍数<1.5x → 定价被砸盘 → L1 上限封顶 40",
        "Pre-IPO最后一轮": "Pre-IPO 最后一轮估值溢价>50% → 估值倒挂 → L1 上限封顶 40",
        "重大未决诉讼": "招股书披露重大未决诉讼 → 法律风险 → L1 上限封顶 40",
        "审计师Tier": "审计师 Tier>2 或近 12 月更换审计师 → 财务可信度风险 → L1 上限封顶 40",
        "股权质押违约": "控股股东近 2 年股权质押违约 → 失信风险 → L1 上限封顶 40",
    }
    for trigger, expl in explanations.items():
        if trigger in reason:
            return f"{reason}; {expl}"
    return reason


# =============================================================================
# Layer 2 explanations
# =============================================================================

def _explain_top_cornerstones(cs: List[CornerstoneInvestor],
                              top_n: int = 3) -> str:
    """选 ticket 最大的 top N 基石, 标注 type label"""
    if not cs:
        return "无基石"
    sorted_cs = sorted(cs, key=lambda c: c.ticket_size_hkd or 0, reverse=True)[:top_n]
    parts = []
    total = sum(c.ticket_size_hkd or 0 for c in cs)
    for c in sorted_cs:
        share = (c.ticket_size_hkd / total) if total > 0 else 0
        type_label = CORNERSTONE_TYPE_LABELS.get(c.type, str(c.type))
        parts.append(f"{c.name} ({type_label.split(' ')[0]}, {share:.0%})")
    return " | ".join(parts)


def _explain_l2_q_weighted(cs: List[CornerstoneInvestor], score: float) -> str:
    return (f"按 ticket 加权的基石平均质量 = {score:.1f}/100. "
            f"Top 3 by ticket: {_explain_top_cornerstones(cs, 3)}")


def _explain_l2_coverage(coverage: float, score: float) -> str:
    if coverage < 0.20:
        band = "<20% 偏低 → 0~40 线性"
    elif coverage < 0.35:
        band = "20~35% 良好 → 40~90 线性"
    elif coverage <= 0.50:
        band = "35~50% 满分区"
    elif coverage <= 0.65:
        band = "50~65% 偏高 → 100→70 (有过度依赖基石嫌疑)"
    else:
        band = ">65% 危险区 → 70→40 (基石救市信号)"
    return f"覆盖率 {coverage:.1%} → {score:.0f} 分; 命中 [{band}]"


def _explain_l2_hhi(hhi_v: float, score: float) -> str:
    if hhi_v < 1500:
        band = "<1500 低集中 → 满分"
    elif hhi_v < 2500:
        band = "1500~2500 中等 → 100→80"
    elif hhi_v < 4000:
        band = "2500~4000 偏高 → 80→50"
    else:
        band = "≥4000 高集中 → 50→20 (受单一基石主导风险)"
    return f"HHI = {hhi_v:.0f} → {score:.0f} 分; 命中 [{band}]"


def _explain_l2_diversity(entropy: float, score: float, n_types: int) -> str:
    return (f"基石类型多样性熵 = {entropy:.2f} (类型数={n_types}, "
            f"max=log2(8)≈3); 直接 ×100 → {score:.0f} 分")


def _explain_l2_pollution(pct: float, score: float) -> str:
    if pct == 0:
        band = "0% 无关联 → 100"
    elif pct <= 0.10:
        band = "0~10% → 100→80"
    elif pct <= 0.25:
        band = "10~25% → 80→50"
    elif pct <= 0.40:
        band = "25~40% → 50→20"
    else:
        band = "≥40% → 20→0 (大量关联方撑场)"
    return f"关联污染率 {pct:.1%} → {score:.0f} 分; 命中 [{band}]"


def _explain_l2_synergy(cs: List[CornerstoneInvestor], score: float) -> str:
    industrial = [c for c in cs if c.type == CornerstoneType.STRATEGIC_INDUSTRIAL]
    return (f"产业资本基石 {len(industrial)} 个 (≥2 满分) → {score:.0f} 分. "
            f"产业方: {', '.join(c.name for c in industrial[:3]) or '无'}")


def _explain_l2_zucou(chinese_pct: float, longterm_pct: float,
                       red_flag: bool) -> str:
    return (f"中资基石占比 {chinese_pct:.1%}, 长线占比 {longterm_pct:.1%}; "
            f"{'⚠ 国资凑数红旗 (中资>70% 且长线<30%) → -10' if red_flag else '✓ 非凑数 → +10'}")


def _explain_l2_cluster_bonus(count: int, mult: float) -> str:
    if mult > 1.0:
        return (f"cluster_count={count} → ×{mult:.2f} 加成 "
                f"(产业资本/家族办公室通过多 SPV 重仓信号)")
    return (f"cluster_count={count}; v3 实证证伪后强制 ×1.0 "
            f"(原 ×1.10/1.15/1.20 加成在 m6 上反向预测)")


def explain_layer2_components(o: IPOOffering,
                              components: Dict[str, Any]) -> Dict[str, str]:
    cs = o.cornerstones or []
    reasons = {}
    if not cs:
        ch = o.listing_chapter.value
        reasons["no_cornerstones"] = (
            f"无基石 ({ch}). 主板/18A/AH 此时 → veto; "
            f"secondary/18C 章节常态可不带基石, 走默认值"
        )
        return reasons

    if "Q_weighted" in components:
        reasons["Q_weighted"] = _explain_l2_q_weighted(cs, components["Q_weighted"])
    if "coverage" in components and "coverage_score" in components:
        reasons["coverage"] = _explain_l2_coverage(
            components["coverage"], components["coverage_score"])
    if "hhi" in components and "hhi_score" in components:
        reasons["hhi"] = _explain_l2_hhi(
            components["hhi"], components["hhi_score"])
    if "diversity_entropy" in components and "diversity_score" in components:
        n_types = len({c.type for c in cs})
        reasons["diversity_entropy"] = _explain_l2_diversity(
            components["diversity_entropy"],
            components["diversity_score"], n_types)
    if "affiliation_pct" in components and "pollution_score" in components:
        reasons["affiliation_pct"] = _explain_l2_pollution(
            components["affiliation_pct"], components["pollution_score"])
    if "synergy_score" in components:
        reasons["synergy_score"] = _explain_l2_synergy(
            cs, components["synergy_score"])
    if "zucou_red_flag" in components:
        reasons["zucou_red_flag"] = _explain_l2_zucou(
            components.get("chinese_pct", 0),
            components.get("longterm_pct", 0),
            bool(components["zucou_red_flag"]))
    if "cluster_bonus" in components:
        reasons["cluster_bonus"] = _explain_l2_cluster_bonus(
            int(components.get("cluster_count", 0)),
            components["cluster_bonus"])
    return reasons


def explain_l2_veto(reason: Optional[str]) -> Optional[str]:
    if not reason:
        return None
    explanations = {
        "无基石": "无基石 (主板/18A/A+H 章节强制要求基石披露) → 强制 veto",
        "关联污染率": "关联方占比超阈值 → Q_e 上限压低",
        "加权基石质量分": "Q_weighted 太低 (主要基石都是 family_office_spv) → Q_e 上限压低",
        "基石数<": "基石数太少且最大单一占比过高 → 集中度风险 → Q_e 上限压低",
    }
    for trigger, expl in explanations.items():
        if trigger in reason:
            return f"{reason}; {expl}"
    return reason


# =============================================================================
# Layer 3 explanations
# =============================================================================

def _explain_vol_risk(sigma: float, risk: float) -> str:
    if sigma < 0.25:
        band = "<25% 低波 → 0.10"
    elif sigma < 0.40:
        band = "25~40% 中波 → 0.10→0.30"
    elif sigma < 0.60:
        band = "40~60% 高波 → 0.30→0.55"
    else:
        band = "≥60% 极高波 → 0.55→0.80"
    return (f"年化波动率代理 (max sector/HSI 60d) = {sigma:.1%} → "
            f"风险值 {risk:.2f}; 命中 [{band}]")


def _explain_val_reversal_risk(pe_pct: float, risk: float) -> str:
    if pe_pct < 0.50:
        band = "<50 分位 → 0.10"
    elif pe_pct < 0.70:
        band = "50~70 分位 → 0.10→0.30"
    elif pe_pct < 0.85:
        band = "70~85 分位 → 0.30→0.55"
    else:
        band = "≥85 分位 → 0.55→0.75 (估值贵, 锁定期内回调风险)"
    return (f"PE 在该公司历史的百分位 = {pe_pct:.0%} → 风险值 {risk:.2f}; "
            f"命中 [{band}]")


def _explain_overhang_risk(ratio: float, risk: float) -> str:
    if ratio < 0.70:
        band = "<0.70 → 0.10 (新股占比高, 解禁压力小)"
    elif ratio < 0.85:
        band = "0.70~0.85 → 0.10→0.30"
    elif ratio < 0.95:
        band = "0.85~0.95 → 0.30→0.50"
    else:
        band = "≥0.95 → 0.50→0.70 (老股占比极高, 解禁后大规模抛压)"
    return (f"overhang_ratio = pre_ipo_shares/post_ipo_shares = {ratio:.2f} → "
            f"风险值 {risk:.2f}; 命中 [{band}]")


def _explain_macro_risk(hsi_pct: float, risk: float) -> str:
    if hsi_pct < 0.20:
        band = "<20 分位 → 0.20 (HSI 估值便宜)"
    elif hsi_pct < 0.50:
        band = "20~50 分位 → 0.20→0.35"
    elif hsi_pct < 0.80:
        band = "50~80 分位 → 0.35→0.55"
    else:
        band = "≥80 分位 → 0.55→0.75 (HSI 估值贵, 系统性回调风险)"
    return f"HSI 估值百分位 = {hsi_pct:.0%} → 风险值 {risk:.2f}; 命中 [{band}]"


def _explain_fund_risk(score: float) -> str:
    return (f"fundamental_risk_score = {score:.2f} (来自 ifind_financials 的"
            f"营收减速/毛利下滑/负债上升等组合; 越高越糟)")


def _explain_peer_lockup_risk(value: float, risk: float) -> str:
    return (f"同行 IPO 锁定期内 max_drawdown 中位 = {value:.1%}; "
            f"重标定后公式 (drawdown - 0.20) / 0.30 → 风险值 {risk:.2f}")


def explain_layer3_components(o: IPOOffering,
                              components: Dict[str, Any]) -> Dict[str, str]:
    reasons = {}
    if "vol_risk" in components and "sigma_6m_proxy" in components:
        reasons["vol_risk"] = _explain_vol_risk(
            components["sigma_6m_proxy"], components["vol_risk"])
    if "val_reversal_risk" in components and o.lockup:
        reasons["val_reversal_risk"] = _explain_val_reversal_risk(
            o.lockup.pe_vs_history_pct, components["val_reversal_risk"])
    if "overhang_risk" in components and o.lockup:
        reasons["overhang_risk"] = _explain_overhang_risk(
            o.lockup.overhang_ratio, components["overhang_risk"])
    if "macro_risk" in components and o.market:
        reasons["macro_risk"] = _explain_macro_risk(
            o.market.hsi_valuation_pct, components["macro_risk"])
    if "fundamental_risk" in components:
        reasons["fundamental_risk"] = _explain_fund_risk(
            components["fundamental_risk"])
    if "peer_lockup_risk" in components and o.lockup:
        reasons["peer_lockup_risk"] = _explain_peer_lockup_risk(
            o.lockup.peer_lockup_avg_drawdown, components["peer_lockup_risk"])
    return reasons


# =============================================================================
# Adjustment + decision band explanations
# =============================================================================

def explain_adjustment(adj_str: str) -> str:
    """对单条 adjustment_applied 解释为什么触发"""
    s = adj_str.lower()
    if "18c 折扣" in adj_str.lower():
        return ("18C 章节默认 ×0.70 折扣: 18C 标的多数估值不可直接比, "
                "且未商业化档历史回报方差大, 模型保守处理")
    if "a+h" in s and "可融券" in adj_str:
        return ("A+H 同名 A 股可融券 ×1.10: 港股侧定价不会脱离 A 股太久 "
                "(可对冲), 给溢价")
    if "第二上市" in adj_str:
        return ("第二上市 ×0.85: 已上市公司在港交所第二上市定价锚定主市场, "
                "缺少 IPO 折扣传统")
    if "关联交易" in adj_str:
        return ("控股股东近 12 月重大关联交易 ×0.85: 治理风险信号, 减分")
    if "regime_gate" in s:
        return ("Regime gate: 过去 90 天港股 IPO 30d 中位收益 < 阈值 "
                "→ 强制 SKIP. 这不是 alpha 信号, 是 model conditional applicability "
                "(当下市场环境不适合用 NACS)")
    if "relationship 仓位激活" in adj_str:
        return ("v3 章节差异化 RELATIONSHIP: 18C/secondary/A+H 的 RELATIONSHIP "
                "实战 m6 mean 显著为正, 激活 0.10 仓位; 主板/18A 维持 0")
    if "ai 镀金" in s or "AI 镀金" in adj_str:
        return ("P0.2 AI 镀金检测: 主题分类为 AI 但实际 AI 业务收入占比低 "
                "(<阈值, 默认 10%); 公司可能在招股书把 AI 概念放头版以拉高估值,"
                " 实际业务跟 AI 主题的相关度有限. 给折扣防止过度仓位.")
    return adj_str


def explain_decision_band(nacs: float, position_pct: float,
                          decision: str) -> str:
    """决策映射: NACS_adj → band → decision/position"""
    bands_repr = [
        ("≥0.55", "FULL", "100%"),
        ("≥0.45", "LARGE", "70%"),
        ("≥0.35", "TRIAL", "40%"),
        ("≥0.25", "RELATIONSHIP", "0% (诊断标签, 章节激活则 10%)"),
        ("<0.25", "SKIP", "0%"),
    ]
    matched = None
    for thresh_label, dec_label, pos_label in bands_repr:
        if dec_label == decision:
            matched = (thresh_label, dec_label, pos_label)
            break
    band_str = " · ".join(f"{t} {d} ({p})" for t, d, p in bands_repr)
    if matched:
        return (f"NACS_adj = {nacs:.4f} 落入 [{matched[0]}] → {matched[1]} "
                f"({matched[2]}). 完整 band 表: {band_str}")
    return f"NACS_adj = {nacs:.4f} → {decision}; band 表: {band_str}"


def explain_formula(Qc: float, Qe: float, Rl: float,
                    nacs_raw: float, nacs_adjusted: float,
                    adjustments: List[str]) -> List[str]:
    """主公式拆解 (按行返回, 模板用 <ol>/<pre> 渲染)"""
    lines = [
        f"NACS_raw = Q_company × Q_ecosystem × (1 - R_lockup)",
        f"        = {Qc:.4f} × {Qe:.4f} × (1 - {Rl:.4f})",
        f"        = {Qc:.4f} × {Qe:.4f} × {1 - Rl:.4f}",
        f"        = {nacs_raw:.4f}",
    ]
    if adjustments:
        cumulative = nacs_raw
        lines.append("")
        lines.append("Adjustments (multiplicative):")
        for adj in adjustments:
            # 提取 ×N.NN 数字
            import re
            m = re.search(r"x([\d.]+)", adj)
            if m:
                mult = float(m.group(1))
                new = min(cumulative * mult, 1.0)
                lines.append(f"  ×{mult:.2f}  {adj}  → {cumulative:.4f} → {new:.4f}")
                cumulative = new
            else:
                lines.append(f"        {adj}")
        lines.append("")
        lines.append(f"NACS_adj = {nacs_adjusted:.4f}")
    else:
        lines.append("")
        lines.append("Adjustments: none → NACS_adj = NACS_raw = "
                     f"{nacs_adjusted:.4f}")
    return lines
