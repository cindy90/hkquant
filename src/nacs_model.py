"""
NACS v1.0 - Net-Adjusted Cornerstone Score
港股IPO基石投资量化决策模型 (Hong Kong IPO Cornerstone Quant Model)

设计目标:
    给定一只即将定价的港股IPO,在询价前1-2周输出三层打分:
        Layer 1: Q_company   - 发行质量分      [0, 1]
        Layer 2: Q_ecosystem - 基石生态分      [0, 1]
        Layer 3: R_lockup    - 锁定期风险分    [0, 1]
    最终决策分:
        NACS = Q_company * Q_ecosystem * (1 - R_lockup)

    NACS -> 仓位建议(占该项目最大ticket size的%):
        >= 0.55  -> 100% 满额认购
        0.45-0.55 -> 70%
        0.35-0.45 -> 40%
        0.25-0.35 -> 15% 关系单
        < 0.25   -> 0%   弃单

约束(本版本):
    - 暂不计入 FX/通道成本
    - 18C 子流程 + NACS x 0.7 折扣
    - A+H 同名A股可融券 -> NACS x 1.10
    - 第二上市 -> NACS x 0.85

Author: NACS Quant Team
Version: 1.0.0
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Tuple


# =============================================================================
# 1. 枚举与常量
# =============================================================================

class ListingChapter(Enum):
    MAIN_BOARD_PROFITABLE = "main_board_profitable"
    A_PLUS_H = "a_plus_h"
    MAIN_BOARD_UNPROFITABLE = "main_board_unprofitable"
    CHAPTER_18A = "18a"
    SECONDARY_LISTING = "secondary"
    CHAPTER_18C_COMMERCIAL = "18c_commercial"
    CHAPTER_18C_PRECOMMERCIAL = "18c_precommercial"
    SPAC_DESPAC = "spac"


CHAPTER_BASE_SCORE: Dict[ListingChapter, float] = {
    ListingChapter.MAIN_BOARD_PROFITABLE:    100.0,
    ListingChapter.A_PLUS_H:                  90.0,
    ListingChapter.MAIN_BOARD_UNPROFITABLE:   70.0,
    ListingChapter.CHAPTER_18A:               65.0,
    ListingChapter.SECONDARY_LISTING:         60.0,
    ListingChapter.CHAPTER_18C_COMMERCIAL:    50.0,
    ListingChapter.CHAPTER_18C_PRECOMMERCIAL: 35.0,
    ListingChapter.SPAC_DESPAC:               25.0,
}


class CornerstoneType(Enum):
    SOVEREIGN_PENSION = "sovereign_pension"
    GLOBAL_LONG_ONLY = "global_long_only"
    TOP_HEDGE_PREIPO = "top_hedge_preipo"
    CHINESE_MUTUAL_INSURANCE = "cn_mutual_insurance"
    STRATEGIC_INDUSTRIAL = "strategic_industrial"
    POLICY_FUND = "policy_fund"
    PE_VC_CONTINUATION = "pe_vc_continuation"
    FAMILY_OFFICE_SPV = "family_office_spv"


TYPE_PRIOR_SCORE: Dict[CornerstoneType, float] = {
    CornerstoneType.SOVEREIGN_PENSION:        100.0,
    CornerstoneType.GLOBAL_LONG_ONLY:          95.0,
    CornerstoneType.TOP_HEDGE_PREIPO:          80.0,
    CornerstoneType.CHINESE_MUTUAL_INSURANCE:  75.0,
    CornerstoneType.STRATEGIC_INDUSTRIAL:      70.0,
    CornerstoneType.POLICY_FUND:               65.0,
    CornerstoneType.PE_VC_CONTINUATION:        55.0,
    CornerstoneType.FAMILY_OFFICE_SPV:         30.0,
}

LONGTERM_TYPES = {
    CornerstoneType.SOVEREIGN_PENSION,
    CornerstoneType.GLOBAL_LONG_ONLY,
    CornerstoneType.CHINESE_MUTUAL_INSURANCE,
}

CHINESE_TYPES = {
    CornerstoneType.CHINESE_MUTUAL_INSURANCE,
    CornerstoneType.POLICY_FUND,
    CornerstoneType.STRATEGIC_INDUSTRIAL,  # 多数为A股上市公司中资属性
}


class SponsorTier(Enum):
    TIER_1 = 1   # 中金/MS/GS/UBS/JPM/华泰国际/CICC
    TIER_2 = 2   # 海通国际/招银国际/农银国际/建银国际等
    TIER_3 = 3   # 其他


class CompanyType(Enum):
    """公司类型分流, 决定 L1.3 用哪一套打分"""
    PROFITABLE = "profitable"
    BIOTECH_18A = "biotech_18a"
    TECH_18C = "tech_18c"


# =============================================================================
# 2. 输入数据类
# =============================================================================

@dataclass
class CornerstoneInvestor:
    """单个基石投资者"""
    name: str
    ticket_size_hkd: float
    type: CornerstoneType
    aum_usd: Optional[float] = None
    hk_ipo_count_5y: int = 0
    hk_ipo_avg_m6_return: Optional[float] = None       # 例: 0.15
    hk_ipo_winrate_m6: Optional[float] = None           # [0, 1]
    lockup_discipline_score: Optional[float] = None     # [0, 1]
    sector_expertise: int = 0                            # 在本IPO行业的历史参与数
    affiliation_flag: bool = False
    affiliation_reason: Optional[str] = None


@dataclass
class ProfitableFundamentals:
    revenue_cagr_3y: Optional[float] = None
    gross_margin_trend: Optional[float] = None          # 3年回归斜率
    roe_avg_3y: Optional[float] = None
    net_debt_to_ebitda: Optional[float] = None
    fcf_positive_years: int = 0                         # 0-3
    top1_customer_pct: Optional[float] = None
    top5_customer_pct: Optional[float] = None


@dataclass
class BiotechFundamentals:
    core_pipeline_phase: str = "Pre"                    # Pre/I/II/III/Approved
    pipeline_count_phase2plus: int = 0
    cash_runway_months: Optional[float] = None
    bd_deals_count_2y: int = 0
    # P1.3: 18A 子领域 (innovative_drug/medical_device/cell_gene/diagnostics);
    # None=未分类, 走 multiplier=1.0 (向后兼容)
    subdomain: Optional[str] = None


@dataclass
class TechC18Fundamentals:
    is_commercial: bool = True
    revenue_latest_hkd: Optional[float] = None
    revenue_growth_yoy: Optional[float] = None
    rd_intensity: Optional[float] = None
    milestone_score: float = 3.0                        # 主观 1-5


@dataclass
class OfferingStructure:
    pricing_in_range: float                             # [0, 1]
    intl_oversubscription: float
    public_oversubscription: float
    clawback_triggered: bool
    greenshoe_pct: float
    offering_size_hkd: float                             # 募资额 (含/不含绿鞋视字段语义)
    pe_at_offer: Optional[float] = None
    pe_peer_median: Optional[float] = None
    last_round_premium: Optional[float] = None
    auditor_tier: int = 1                               # 1=四大, 2=本地大所, 3=其他
    auditor_changed_within_12m: bool = False
    material_litigation: bool = False
    controlling_shareholder_pledge_default: bool = False
    # P1.1: 总市值 (post_ipo_shares × offer_price_hkd); None=未知 → modifier 跳过
    mkt_cap_at_offer_hkd: Optional[float] = None


@dataclass
class SponsorInfo:
    primary_sponsor: str
    primary_tier: SponsorTier
    joint_sponsor_count: int = 1
    sponsor_d30_winrate_pct_rank: Optional[float] = None
    sponsor_breakage_rate_pct_rank: Optional[float] = None
    sponsor_avg_d30_pct_rank: Optional[float] = None


@dataclass
class MarketEnvironment:
    hsi_60d_return: float
    hsi_60d_vol_annualized: float
    hsi_60d_vol_pct_rank: float                         # [0, 1]
    hsi_valuation_pct: float                            # [0, 1]
    hk_ipo_30d_avg_d30: float
    hk_ipo_30d_breakage_rate: float                     # [0, 1]
    southbound_30d_net_normalized: float                # [-1, 1]
    sector_60d_vol_annualized: float = 0.30


@dataclass
class LockupContext:
    lockup_months: int = 6
    overhang_ratio: float = 1.0
    fundamental_risk_score: float = 0.3                 # [0, 1]
    peer_lockup_avg_drawdown: float = 0.10
    pe_vs_history_pct: float = 0.5                      # [0, 1]


@dataclass
class IPOOffering:
    """整合一只IPO的全部输入"""
    company_name: str
    stock_code: str
    listing_chapter: ListingChapter
    company_type: CompanyType
    is_a_h: bool = False
    a_share_short_borrowable: bool = False              # A+H专用: A股是否可融券
    is_stock_connect_eligible_expected: bool = True
    weighted_voting_rights: bool = False
    has_related_party_tx_recent: bool = False           # 控股股东近12月重大关联交易

    cornerstones: List[CornerstoneInvestor] = field(default_factory=list)
    offering: Optional[OfferingStructure] = None
    sponsor: Optional[SponsorInfo] = None
    market: Optional[MarketEnvironment] = None
    lockup: Optional[LockupContext] = None

    profitable: Optional[ProfitableFundamentals] = None
    biotech: Optional[BiotechFundamentals] = None
    tech18c: Optional[TechC18Fundamentals] = None

    # v7 新增: 制度门控 + 簇基石信号
    regime_score: Optional[float] = None        # pricing_date 时点的市场制度分 (None=不启用门控)
    cluster_cornerstone_count: int = 0          # 同一 ultimate_holder ≥2 的簇基石总数

    # P0.1 新增: 主题情绪 (从 themes/heat_today.json 注入; None=不分类/无数据, 不影响打分)
    theme_id: Optional[str] = None              # classify_deal_to_theme 输出 (审计用)
    theme_heat_score: Optional[int] = None      # 0-100, _score_l1_6_market modifier 输入

    # P0.2 新增: AI 镀金检测 (theme∈AI_THEMES + ai_revenue_pct<threshold → ×0.85)
    ai_revenue_pct: Optional[float] = None      # 0-1; 来自 deal YAML 或 ai_revenue_manual.json


# =============================================================================
# 3. 输出数据类
# =============================================================================

@dataclass
class LayerBreakdown:
    """单层打分明细 (供归因/审计)"""
    name: str
    raw_score: float                                    # 0-100
    normalized: float                                   # 0-1
    components: Dict[str, float] = field(default_factory=dict)
    veto_triggered: bool = False
    veto_reason: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    # 每个子项的人类可读"为什么这么打分" (key 与 components 同名, 值为 sentence)
    reasons: Dict[str, str] = field(default_factory=dict)


@dataclass
class NACSResult:
    company_name: str
    stock_code: str
    Q_company: float
    Q_ecosystem: float
    R_lockup: float
    nacs_raw: float                                     # Q_c * Q_e * (1 - R_l)
    nacs_adjusted: float                                # 加 18C/A+H/二次上市 修正
    position_pct: float                                 # 仓位建议 0-1
    decision: str                                       # FULL / LARGE / TRIAL / RELATIONSHIP / SKIP

    layer1: LayerBreakdown = field(default_factory=lambda: LayerBreakdown("L1", 0, 0))
    layer2: LayerBreakdown = field(default_factory=lambda: LayerBreakdown("L2", 0, 0))
    layer3: LayerBreakdown = field(default_factory=lambda: LayerBreakdown("L3", 0, 0))

    adjustments_applied: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # 决策链路解释 (compute_nacs 末尾填; 模板按行展示)
    decision_rationale: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "company": self.company_name,
            "code": self.stock_code,
            "Q_company": round(self.Q_company, 4),
            "Q_ecosystem": round(self.Q_ecosystem, 4),
            "R_lockup": round(self.R_lockup, 4),
            "NACS_raw": round(self.nacs_raw, 4),
            "NACS_adjusted": round(self.nacs_adjusted, 4),
            "position_pct": round(self.position_pct, 3),
            "decision": self.decision,
            "adjustments": list(self.adjustments_applied),
            "warnings": list(self.warnings),
            "decision_rationale": list(self.decision_rationale),
        }


# =============================================================================
# 4. 数学辅助函数
# =============================================================================

def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b not in (0, 0.0, None) else default


def linear_score(x: float, lo: float, hi: float, lo_score: float = 0.0,
                 hi_score: float = 100.0) -> float:
    """x 在 [lo, hi] 之间线性映射到 [lo_score, hi_score]; 区间外裁剪"""
    if hi == lo:
        return (lo_score + hi_score) / 2.0
    t = clip((x - lo) / (hi - lo), 0.0, 1.0)
    return lo_score + t * (hi_score - lo_score)


def tanh_score(x: float, scale: float, midpoint: float = 0.0,
               lo_score: float = 0.0, hi_score: float = 100.0) -> float:
    """tanh 平滑打分 (动量/南向资金类用), x>>midpoint 趋近 hi_score"""
    t = math.tanh((x - midpoint) / scale)            # [-1, 1]
    return lo_score + (hi_score - lo_score) * (t + 1) / 2.0


def log_scale_score(x: float, cap: float, base: float = math.e,
                    hi_score: float = 100.0) -> float:
    """对数标度打分: 适合超额认购倍数等量级跨度大的指标"""
    if x is None or x <= 0:
        return 0.0
    t = math.log(1 + x, base) / math.log(1 + cap, base)
    return clip(t, 0.0, 1.0) * hi_score


def u_shape_score(x: float, low_optimal: float, high_optimal: float,
                  low_floor: float, high_ceil: float) -> float:
    """U型(实为倒U)打分: [low_optimal, high_optimal] 是顶部100分,
       [low_floor, high_ceil] 之外为0分, 之间线性"""
    if x < low_floor or x > high_ceil:
        return 0.0
    if low_optimal <= x <= high_optimal:
        return 100.0
    if x < low_optimal:
        return linear_score(x, low_floor, low_optimal, 0.0, 100.0)
    return linear_score(x, high_optimal, high_ceil, 100.0, 0.0)


def shannon_entropy(weights: List[float]) -> float:
    """归一化权重的 Shannon 熵; 输出已除以 ln(N) 归到 [0, 1]"""
    weights = [w for w in weights if w > 0]
    n = len(weights)
    if n <= 1:
        return 0.0
    total = sum(weights)
    if total <= 0:
        return 0.0
    p = [w / total for w in weights]
    h = -sum(pi * math.log(pi) for pi in p)
    return h / math.log(n)


def hhi(weights: List[float]) -> float:
    """Herfindahl-Hirschman Index, 单位×10000 (跟反垄断/IPO研究口径一致)"""
    total = sum(weights)
    if total <= 0:
        return 0.0
    shares = [w / total for w in weights]
    return sum(s * s for s in shares) * 10000.0


# =============================================================================
# 5. Layer 1: 发行质量分 Q_company
# =============================================================================

# 5.1 估值合理性 (权重 25)
def _score_pe_discount(pe_at_offer: Optional[float],
                       pe_peer: Optional[float]) -> float:
    """估值折价分: peer 折价>=30% 满分, 持平 50, 溢价>=20% 0分"""
    if pe_at_offer is None or pe_peer is None or pe_peer <= 0:
        return 50.0
    discount = (pe_peer - pe_at_offer) / pe_peer
    if discount >= 0.30:
        return 100.0
    if discount >= 0.0:
        # [0, 0.30] -> [50, 100]
        return linear_score(discount, 0.0, 0.30, 50.0, 100.0)
    if discount >= -0.20:
        # [-0.20, 0] -> [0, 50]
        return linear_score(discount, -0.20, 0.0, 0.0, 50.0)
    return 0.0


def _score_last_round_premium(last_round_premium: Optional[float]) -> float:
    """对Pre-IPO最后一轮的折溢价: 折价 100, 持平 60, 溢价30%-> ~30, 溢价>50% -> 0.

    ⚠ 当前 ipo_master.last_round_premium 全表 NULL (数据未灌), 所以这里 100%
    走 60.0 中性默认值 → L1.1 估值评分中 last_round_premium 项实际未启用.
    L1 否决条款 'last_round_premium > 0.50 → 强制 SKIP' 同样未启用 (见 _check_l1_veto).
    数据补齐 (来自 wind/F&S 报告) 后自动生效, 无需改代码.
    """
    if last_round_premium is None:
        return 60.0
    p = last_round_premium
    if p <= -0.10:
        return 100.0
    if p <= 0.0:
        return linear_score(p, -0.10, 0.0, 100.0, 60.0)
    if p <= 0.30:
        return linear_score(p, 0.0, 0.30, 60.0, 30.0)
    if p <= 0.50:
        return linear_score(p, 0.30, 0.50, 30.0, 0.0)
    return 0.0


def _score_l1_1_valuation(o: OfferingStructure,
                          ctype: CompanyType) -> Tuple[float, Dict[str, float]]:
    if ctype == CompanyType.TECH_18C:
        # 18C用 last_round_premium 单项 (PE/PS不可比)
        s = _score_last_round_premium(o.last_round_premium)
        return s, {"last_round_premium_score": s}
    s_pe = _score_pe_discount(o.pe_at_offer, o.pe_peer_median)
    s_pre = _score_last_round_premium(o.last_round_premium)
    score = 0.6 * s_pe + 0.4 * s_pre
    return score, {"pe_discount_score": s_pe, "preipo_premium_score": s_pre}


# 5.2 保荐人质量 (权重 15)
def _score_l1_2_sponsor(s: SponsorInfo) -> Tuple[float, Dict[str, float]]:
    # 默认: 用百分位排名; 缺失时用 Tier 推断
    win = s.sponsor_d30_winrate_pct_rank
    brk = s.sponsor_breakage_rate_pct_rank
    avg = s.sponsor_avg_d30_pct_rank

    def _from_tier(t: SponsorTier) -> float:
        return {SponsorTier.TIER_1: 0.85, SponsorTier.TIER_2: 0.55,
                SponsorTier.TIER_3: 0.30}[t]

    win = win if win is not None else _from_tier(s.primary_tier)
    avg = avg if avg is not None else _from_tier(s.primary_tier)
    brk = brk if brk is not None else (1 - _from_tier(s.primary_tier))

    score = 100.0 * (0.4 * win + 0.3 * (1 - brk) + 0.3 * avg)

    # 单一保荐人 + Tier1 加5; 联席 + 含 Tier1 微调
    if s.joint_sponsor_count == 1 and s.primary_tier == SponsorTier.TIER_1:
        score += 5.0
    elif s.joint_sponsor_count >= 2 and s.primary_tier == SponsorTier.TIER_1:
        score += 2.0
    elif s.joint_sponsor_count == 1 and s.primary_tier == SponsorTier.TIER_3:
        score -= 5.0

    score = clip(score, 0.0, 100.0)
    return score, {"win_pct_rank": win, "brk_pct_rank": brk,
                   "avg_d30_pct_rank": avg, "tier": s.primary_tier.value}


# 5.3 基本面质量 (权重 25) - 三套子流程
def _score_l1_3_profitable(f: ProfitableFundamentals) -> Tuple[float, Dict[str, float]]:
    # 30% revenue_cagr 封顶; ROE 20% 封顶; net_debt/EBITDA 4 封顶 (反向)
    rev = clip(safe_div(f.revenue_cagr_3y or 0.0, 0.30), 0.0, 1.0) * 20.0
    gm = (15.0 if (f.gross_margin_trend or 0.0) > 0 else 4.5)
    roe = clip(safe_div(f.roe_avg_3y or 0.0, 0.20), 0.0, 1.0) * 25.0
    nd = (1 - clip((f.net_debt_to_ebitda or 0.0) / 4.0, 0.0, 1.0)) * 20.0
    fcf = (f.fcf_positive_years / 3.0) * 20.0
    total = rev + gm + roe + nd + fcf

    # P1.2: 盈利质量 tier multiplier
    cfg = _get_cfg()
    pt = cfg.layer1_profitability_tier if cfg is not None else None
    enabled = pt.enabled if pt is not None else True
    roe_th = pt.persistent_roe_threshold if pt is not None else 0.15
    fcf_min = pt.persistent_fcf_min_years if pt is not None else 3
    pers_mult = pt.persistent_multiplier if pt is not None else 1.10
    fresh_max = pt.fresh_fcf_max_years if pt is not None else 1
    fresh_mult = pt.fresh_multiplier if pt is not None else 0.90

    profit_tier = "moderate"
    profit_tier_mult = 1.0
    if enabled:
        roe_val = f.roe_avg_3y if f.roe_avg_3y is not None else 0.0
        if roe_val >= roe_th and f.fcf_positive_years >= fcf_min:
            profit_tier = "persistent"
            profit_tier_mult = pers_mult
        elif f.fcf_positive_years <= fresh_max:
            profit_tier = "fresh"
            profit_tier_mult = fresh_mult
    total *= profit_tier_mult

    return clip(total, 0.0, 100.0), {
        "revenue_score": rev, "margin_trend_score": gm, "roe_score": roe,
        "net_debt_score": nd, "fcf_score": fcf,
        "profit_tier": profit_tier,
        "profit_tier_multiplier": profit_tier_mult,
    }


_PHASE_SCORE = {"Pre": 10, "I": 30, "II": 60, "III": 85, "Approved": 100}

def _score_l1_3_biotech(b: BiotechFundamentals) -> Tuple[float, Dict[str, float]]:
    phase = _PHASE_SCORE.get(b.core_pipeline_phase, 10) / 100.0 * 35
    pipe = clip(b.pipeline_count_phase2plus / 3.0, 0.0, 1.0) * 20
    runway = clip((b.cash_runway_months or 0.0) / 24.0, 0.0, 1.0) * 25
    bd = clip(b.bd_deals_count_2y / 2.0, 0.0, 1.0) * 20
    total = phase + pipe + runway + bd

    # P1.3: 18A 子领域 multiplier
    cfg = _get_cfg()
    sd = cfg.layer1_biotech_subdomain if cfg is not None else None
    enabled = sd.enabled if sd is not None else True
    sub_label = b.subdomain if b.subdomain else "unknown"
    sub_mult = 1.0
    if enabled and b.subdomain:
        mults = sd.multipliers if sd is not None else {}
        sub_mult = mults.get(b.subdomain, 1.0)
    total *= sub_mult

    return clip(total, 0.0, 100.0), {
        "phase_score": phase, "pipeline_score": pipe,
        "runway_score": runway, "bd_score": bd,
        "subdomain": sub_label,
        "subdomain_multiplier": sub_mult,
    }


def _score_l1_3_18c(t: TechC18Fundamentals) -> Tuple[float, Dict[str, float]]:
    """18C商业化档评分; 未商业化档上限60"""
    if t.is_commercial:
        rev_growth = clip((t.revenue_growth_yoy or 0.0) / 0.50, 0.0, 1.0) * 30
        ms = (t.milestone_score / 5.0) * 25
        rd = clip((t.rd_intensity or 0.0) / 0.20, 0.0, 1.0) * 25
        total = rev_growth + ms + rd + 20  # 余下20分给基础"已商业化"
        return clip(total, 0.0, 100.0), {
            "rev_growth_score": rev_growth, "milestone_score": ms,
            "rd_score": rd, "base": 20,
        }
    # 未商业化
    ms = (t.milestone_score / 5.0) * 40
    runway_proxy = 20  # 假设有最低runway
    score = ms + runway_proxy
    return clip(score, 0.0, 60.0), {  # 强制上限60
        "milestone_score": ms, "runway_proxy": runway_proxy,
        "note": "precommercial_capped_at_60",
    }


# 5.4 发行结构 (权重 15)
def _score_l1_4_offering(o: OfferingStructure) -> Tuple[float, Dict[str, float]]:
    # 区间利用率: 中位偏上(0.5-0.8)最佳; 上限80; 下限40
    pir = o.pricing_in_range
    if 0.5 <= pir <= 0.8:
        range_s = 100.0
    elif pir > 0.8:
        range_s = linear_score(pir, 0.8, 1.0, 100.0, 80.0)
    else:
        range_s = linear_score(pir, 0.0, 0.5, 40.0, 100.0)
    range_score = range_s / 100.0 * 20.0

    intl = log_scale_score(o.intl_oversubscription, cap=20.0) / 100.0 * 30.0
    public = log_scale_score(o.public_oversubscription, cap=100.0) / 100.0 * 15.0
    clawback = 5.0 if o.clawback_triggered else 2.5

    # 绿鞋: 接近15%最佳
    gs = u_shape_score(o.greenshoe_pct, 0.13, 0.15, 0.0, 0.20) / 100.0 * 15.0

    # 发行规模: U型, 5亿HKD以下太小, 200亿以上稀释强
    size_b = o.offering_size_hkd / 1e9
    size_score = u_shape_score(size_b, 1.5, 8.0, 0.3, 30.0) / 100.0 * 15.0

    total = range_score + intl + public + clawback + gs + size_score

    # P1.1: 总市值分桶 modifier (跟 size_score 独立, 不重复扣)
    mkt_cap_mod = 0.0
    mkt_cap_label = "n/a"
    if o.mkt_cap_at_offer_hkd is not None:
        cfg = _get_cfg()
        mc = cfg.layer1_offering_mkt_cap if cfg is not None else None
        enabled = mc.enabled if mc is not None else True
        small_th = mc.small_cap_threshold_hkd if mc is not None else 5e9
        small_pen = mc.small_cap_penalty if mc is not None else -10.0
        mega_th = mc.mega_cap_threshold_hkd if mc is not None else 5e11
        mega_pen = mc.mega_cap_penalty if mc is not None else -5.0
        if enabled:
            if o.mkt_cap_at_offer_hkd < small_th:
                mkt_cap_mod = small_pen
                mkt_cap_label = "small_cap"
            elif o.mkt_cap_at_offer_hkd > mega_th:
                mkt_cap_mod = mega_pen
                mkt_cap_label = "mega_cap"
            else:
                mkt_cap_label = "mid_cap"
    total += mkt_cap_mod

    # 红旗: 国际倍数<1.5x 强制压低
    if o.intl_oversubscription < 1.5:
        total = min(total, 30.0)

    return clip(total, 0.0, 100.0), {
        "range_score": range_score, "intl_score": intl, "public_score": public,
        "clawback_score": clawback, "greenshoe_score": gs, "size_score": size_score,
        "mkt_cap_modifier": mkt_cap_mod,
        "mkt_cap_at_offer_hkd": float(o.mkt_cap_at_offer_hkd) if o.mkt_cap_at_offer_hkd else 0.0,
        "mkt_cap_bucket": mkt_cap_label,
    }


# 5.5 制度路径 (权重 10)
def _score_l1_5_chapter(o: IPOOffering) -> Tuple[float, Dict[str, float]]:
    base = CHAPTER_BASE_SCORE[o.listing_chapter]
    bonus = 10.0 if o.is_stock_connect_eligible_expected else -5.0
    wvr = -5.0 if o.weighted_voting_rights else 0.0
    score = clip(base + bonus + wvr, 0.0, 100.0)
    return score, {"base": base, "connect_bonus": bonus, "wvr_penalty": wvr}


# 5.6 市场环境 (权重 10)
def _score_l1_6_market(m: MarketEnvironment,
                       theme_heat_score: Optional[int] = None
                       ) -> Tuple[float, Dict[str, float]]:
    """
    Args:
        m: panel-level 市场环境
        theme_heat_score: P0.1 (deal-level): 主题情绪 0-100; None 时不参与
    """
    momentum = tanh_score(m.hsi_60d_return, scale=0.10) / 100.0 * 20.0
    low_vol = (1 - m.hsi_60d_vol_pct_rank) * 20.0
    ipo_avg = tanh_score(m.hk_ipo_30d_avg_d30, scale=0.05) / 100.0 * 25.0
    ipo_brk = (1 - m.hk_ipo_30d_breakage_rate) * 20.0
    south = tanh_score(m.southbound_30d_net_normalized, scale=0.5) / 100.0 * 15.0
    total = momentum + low_vol + ipo_avg + ipo_brk + south

    # P0.1: 主题情绪 modifier (config 阈值)
    theme_heat_mod = 0.0
    if theme_heat_score is not None:
        cfg = _get_cfg()
        th = cfg.layer1_market_theme_heat if cfg is not None else None
        enabled = th.enabled if th is not None else True
        over_thresh = th.overheated_threshold if th is not None else 80
        over_penalty = th.overheated_penalty if th is not None else -5.0
        trough_thresh = th.trough_threshold if th is not None else 40
        trough_bonus = th.trough_bonus if th is not None else 3.0
        if enabled:
            if theme_heat_score >= over_thresh:
                theme_heat_mod = over_penalty
            elif theme_heat_score < trough_thresh:
                theme_heat_mod = trough_bonus
    total += theme_heat_mod

    return clip(total, 0.0, 100.0), {
        "momentum": momentum, "low_vol": low_vol, "ipo_30d_avg": ipo_avg,
        "ipo_30d_brk": ipo_brk, "southbound": south,
        "theme_heat_modifier": theme_heat_mod,
        "theme_heat_score": float(theme_heat_score) if theme_heat_score is not None else 0.0,
    }


# 5.x 否决条款检查
def _check_l1_veto(o: IPOOffering) -> Tuple[bool, Optional[str]]:
    off = o.offering
    if off is None:
        return False, None
    cfg = _get_cfg()
    v = cfg.layer1_vetoes if cfg is not None else None
    intl_min = v.intl_oversub_min if v else 1.5
    premium_max = v.last_round_premium_max if v else 0.50
    auditor_max = v.auditor_tier_max_allowed if v else 2

    if off.intl_oversubscription < intl_min:
        return True, f"国际配售认购倍数<{intl_min}x"
    if off.last_round_premium is not None and off.last_round_premium > premium_max:
        return True, f"Pre-IPO最后一轮估值溢价>{premium_max:.0%}"
    if off.material_litigation:
        return True, "招股书披露重大未决诉讼"
    if off.auditor_tier > auditor_max or off.auditor_changed_within_12m:
        return True, f"审计师Tier>{auditor_max}或近12月更换审计师"
    if off.controlling_shareholder_pledge_default:
        return True, "控股股东近2年股权质押违约"
    return False, None


def score_layer1_company(o: IPOOffering) -> LayerBreakdown:
    assert o.offering and o.sponsor and o.market, "Layer 1 需要 offering/sponsor/market"
    s11, c11 = _score_l1_1_valuation(o.offering, o.company_type)

    s13: float
    c13: Dict[str, float]
    if o.company_type == CompanyType.PROFITABLE:
        assert o.profitable, "需提供 ProfitableFundamentals"
        s13, c13 = _score_l1_3_profitable(o.profitable)
    elif o.company_type == CompanyType.BIOTECH_18A:
        assert o.biotech, "需提供 BiotechFundamentals"
        s13, c13 = _score_l1_3_biotech(o.biotech)
    else:
        assert o.tech18c, "需提供 TechC18Fundamentals"
        s13, c13 = _score_l1_3_18c(o.tech18c)

    s12, c12 = _score_l1_2_sponsor(o.sponsor)
    s14, c14 = _score_l1_4_offering(o.offering)
    s15, c15 = _score_l1_5_chapter(o)
    s16, c16 = _score_l1_6_market(o.market, theme_heat_score=o.theme_heat_score)

    cfg = _get_cfg()
    w = cfg.layer1_weights if cfg is not None else None
    if w is not None:
        raw = (w.valuation * s11 + w.sponsor * s12 + w.fundamentals * s13
               + w.offering * s14 + w.chapter * s15 + w.market * s16)
    else:
        raw = (0.25 * s11 + 0.15 * s12 + 0.25 * s13
               + 0.15 * s14 + 0.10 * s15 + 0.10 * s16)

    veto, reason = _check_l1_veto(o)
    if veto:
        veto_cap = (cfg.layer1_vetoes.veto_score_cap
                    if cfg is not None else 40.0)
        raw = min(raw, veto_cap)

    breakdown = LayerBreakdown(
        name="Q_company",
        raw_score=raw,
        normalized=raw / 100.0,
        components={
            "L1.1_valuation": s11, "L1.2_sponsor": s12, "L1.3_fundamentals": s13,
            "L1.4_offering": s14, "L1.5_chapter": s15, "L1.6_market": s16,
            **{f"_{k}": v for k, v in c11.items()},
            **{f"_{k}": v for k, v in c12.items() if isinstance(v, (int, float))},
            **{f"_{k}": v for k, v in c13.items()},
            **{f"_{k}": v for k, v in c14.items()},
            **{f"_{k}": v for k, v in c15.items()},
            **{f"_{k}": v for k, v in c16.items()},
        },
        veto_triggered=veto,
        veto_reason=reason,
    )
    return breakdown


# =============================================================================
# 6. Layer 2: 基石生态分 Q_ecosystem
# =============================================================================

def score_individual_cornerstone(c: CornerstoneInvestor) -> float:
    """单个基石的Q_i (0-100)"""
    type_score = TYPE_PRIOR_SCORE[c.type] / 100.0 * 25.0

    aum_score = log_scale_score(
        (c.aum_usd or 0) / 1e9, cap=500.0  # 单位:十亿美元, 500B AUM封顶
    ) / 100.0 * 15.0

    repeat = clip(c.hk_ipo_count_5y / 10.0, 0.0, 1.0) * 15.0

    # 历史M+6回报: 把回报值映射成百分位的简化处理
    ret = c.hk_ipo_avg_m6_return
    if ret is None:
        ret_score = 7.5  # 中性
    else:
        # +20%以上算顶, -10%以下算底
        ret_score = linear_score(ret, -0.10, 0.20, 0.0, 15.0)

    win = (c.hk_ipo_winrate_m6 if c.hk_ipo_winrate_m6 is not None else 0.5) * 10.0
    disc = (c.lockup_discipline_score if c.lockup_discipline_score is not None else 0.5) * 15.0
    sector = clip(c.sector_expertise / 3.0, 0.0, 1.0) * 5.0

    affiliation_penalty = 30.0 if c.affiliation_flag else 0.0

    total = type_score + aum_score + repeat + ret_score + win + disc + sector
    total -= affiliation_penalty
    return clip(total, 0.0, 100.0)


def _score_l2_coverage(coverage: float) -> float:
    """覆盖率U型: 20%-35% 优, 35%-50% 满分, >65% 危险"""
    if coverage < 0.20:
        return linear_score(coverage, 0.0, 0.20, 0.0, 40.0)
    if coverage < 0.35:
        return linear_score(coverage, 0.20, 0.35, 40.0, 90.0)
    if coverage <= 0.50:
        return linear_score(coverage, 0.35, 0.50, 90.0, 100.0)
    if coverage <= 0.65:
        return linear_score(coverage, 0.50, 0.65, 100.0, 70.0)
    return linear_score(coverage, 0.65, 0.85, 70.0, 40.0)


def _score_hhi(h: float) -> float:
    if h < 1500:
        return 100.0
    if h < 2500:
        return linear_score(h, 1500, 2500, 100.0, 80.0)
    if h < 4000:
        return linear_score(h, 2500, 4000, 80.0, 50.0)
    return linear_score(h, 4000, 8000, 50.0, 20.0)


def _score_pollution(pct: float) -> float:
    """关联污染率: 0%->100, 10%->80, 25%->50, 40%+->0"""
    if pct <= 0.0:
        return 100.0
    if pct <= 0.10:
        return linear_score(pct, 0.0, 0.10, 100.0, 80.0)
    if pct <= 0.25:
        return linear_score(pct, 0.10, 0.25, 80.0, 50.0)
    if pct <= 0.40:
        return linear_score(pct, 0.25, 0.40, 50.0, 20.0)
    return linear_score(pct, 0.40, 0.60, 20.0, 0.0)


def _score_synergy(cs: List[CornerstoneInvestor]) -> float:
    """战略协同分代理: 这里简化用'产业资本基石个数/2'封顶, 实战应替换为 embedding 语义相似度"""
    industrial = [c for c in cs if c.type == CornerstoneType.STRATEGIC_INDUSTRIAL]
    return clip(len(industrial) / 2.0, 0.0, 1.0) * 100.0


def score_layer2_ecosystem(o: IPOOffering) -> LayerBreakdown:
    cs = o.cornerstones
    assert o.offering, "Layer 2 需要 offering"
    notes: List[str] = []
    veto = False
    veto_reason = None

    if not cs:
        # P2-#1 章节差异化无基石处理:
        # - secondary (SPO): 二次发行本就不需基石, 给中性默认 0.40
        # - 18C: 港股 18C 章节不强制披露基石, 无基石不应直接归零
        # - 其他 (main_board / 18a / a_plus_h): 保留原 veto (无基石确为负信号,
        #   且数据显示有基石的实战回报显著更高)
        # 数据支撑: P1-#11 后回测显示
        #   18c_commercial 全样本 mean60d+107%, 但模型 12/13 SKIP (因为 6 只无基石被 veto)
        #   secondary 全样本 mean60d+41%, 但模型 3/3 SKIP (100% 无基石被 veto)
        ch = o.listing_chapter
        no_cs_default = None
        if ch == ListingChapter.SECONDARY_LISTING:
            no_cs_default = 0.40
        elif ch == ListingChapter.CHAPTER_18C_COMMERCIAL:
            no_cs_default = 0.45
        elif ch == ListingChapter.CHAPTER_18C_PRECOMMERCIAL:
            no_cs_default = 0.40
        if no_cs_default is not None:
            return LayerBreakdown(
                name="Q_ecosystem",
                raw_score=no_cs_default * 100.0,
                normalized=no_cs_default,
                components={"reason": "no_cornerstones_chapter_default",
                            "chapter": ch.value, "default": no_cs_default},
                veto_triggered=False,
                notes=[f"{ch.value} 无基石(章节常态), 用默认 {no_cs_default}"],
            )
        return LayerBreakdown(
            name="Q_ecosystem", raw_score=0.0, normalized=0.0,
            components={"reason": "no_cornerstones"}, veto_triggered=True,
            veto_reason="无基石投资者",
        )

    weights = [c.ticket_size_hkd for c in cs]
    total_ticket = sum(weights)
    coverage = total_ticket / o.offering.offering_size_hkd

    individual_scores = [score_individual_cornerstone(c) for c in cs]
    Q_weighted = (sum(qi * w for qi, w in zip(individual_scores, weights))
                  / total_ticket) if total_ticket > 0 else 0.0

    cov_s = _score_l2_coverage(coverage)
    hhi_v = hhi(weights)
    hhi_s = _score_hhi(hhi_v)

    # 类型多样性熵
    type_weights: Dict[CornerstoneType, float] = {}
    for c in cs:
        type_weights[c.type] = type_weights.get(c.type, 0.0) + c.ticket_size_hkd
    div_entropy = shannon_entropy(list(type_weights.values()))
    div_score = div_entropy * 100.0

    # 关联污染率
    affil_amount = sum(c.ticket_size_hkd for c in cs if c.affiliation_flag)
    affil_pct = affil_amount / total_ticket if total_ticket > 0 else 0.0
    pol_s = _score_pollution(affil_pct)

    syn_s = _score_synergy(cs)

    # 中资/长线解耦
    chinese_amount = sum(c.ticket_size_hkd for c in cs if c.type in CHINESE_TYPES)
    longterm_amount = sum(c.ticket_size_hkd for c in cs if c.type in LONGTERM_TYPES)
    chinese_pct = chinese_amount / total_ticket if total_ticket > 0 else 0.0
    longterm_pct = longterm_amount / total_ticket if total_ticket > 0 else 0.0
    zucou_red_flag = (chinese_pct > 0.70) and (longterm_pct < 0.30)
    if zucou_red_flag:
        notes.append(f"国资凑数红旗: 中资占比{chinese_pct:.1%}, 长线占比{longterm_pct:.1%}")
    zucou_score = -10.0 if zucou_red_flag else 10.0

    cfg = _get_cfg()
    w2 = cfg.layer2_weights if cfg is not None else None
    if w2 is not None:
        raw = (w2.q_weighted * Q_weighted
               + w2.coverage * cov_s
               + w2.hhi * hhi_s
               + w2.diversity * div_score
               + w2.pollution * pol_s
               + w2.synergy * syn_s
               + w2.zucou * (zucou_score + 10.0) * 5.0)
    else:
        raw = (0.30 * Q_weighted
               + 0.15 * cov_s
               + 0.10 * hhi_s
               + 0.10 * div_score
               + 0.20 * pol_s
               + 0.10 * syn_s
               + 0.05 * (zucou_score + 10.0) * 5.0)

    # 否决条款 (v1.2: 放宽阈值, 因 affil 是启发式派生偏严)
    v2 = cfg.layer2_vetoes if cfg is not None else None
    affil_max = v2.affiliation_pct_max if v2 else 0.50
    affil_cap = v2.affiliation_score_cap if v2 else 30.0
    qw_min = v2.q_weighted_min if v2 else 30.0
    qw_cap = v2.q_weighted_score_cap if v2 else 40.0
    sm_n = v2.small_cs_count_threshold if v2 else 3
    sm_share = v2.small_cs_max_share if v2 else 0.60
    sm_cap = v2.small_cs_score_cap if v2 else 35.0

    if affil_pct > affil_max:
        raw = min(raw, affil_cap)
        veto = True
        veto_reason = f"关联污染率{affil_pct:.1%}>{affil_max:.0%}"
    if Q_weighted < qw_min:
        raw = min(raw, qw_cap)
        if not veto:
            veto = True
            veto_reason = f"加权基石质量分{Q_weighted:.1f}<{qw_min}"
    max_share = max(weights) / total_ticket if total_ticket > 0 else 0
    if len(cs) < sm_n and max_share > sm_share:
        raw = min(raw, sm_cap)
        if not veto:
            veto = True
            veto_reason = f"基石数<{sm_n}且最大单一占比{max_share:.1%}>{sm_share:.0%}"

    # ---- v7: cluster bonus ----
    cluster_bonus = _cluster_bonus_multiplier(o.cluster_cornerstone_count)
    if cluster_bonus > 1.0 and not veto:
        raw_pre = raw
        raw = min(100.0, raw * cluster_bonus)
        notes.append(
            f"cluster_bonus×{cluster_bonus:.2f} "
            f"(簇基石{o.cluster_cornerstone_count}: {raw_pre:.1f}→{raw:.1f})"
        )

    breakdown = LayerBreakdown(
        name="Q_ecosystem",
        raw_score=clip(raw, 0.0, 100.0),
        normalized=clip(raw, 0.0, 100.0) / 100.0,
        components={
            "Q_weighted": Q_weighted,
            "coverage": coverage,
            "coverage_score": cov_s,
            "hhi": hhi_v,
            "hhi_score": hhi_s,
            "diversity_entropy": div_entropy,
            "diversity_score": div_score,
            "affiliation_pct": affil_pct,
            "pollution_score": pol_s,
            "synergy_score": syn_s,
            "chinese_pct": chinese_pct,
            "longterm_pct": longterm_pct,
            "zucou_red_flag": float(zucou_red_flag),
            "n_cornerstones": float(len(cs)),
            "max_single_share": max_share,
            "cluster_count": float(o.cluster_cornerstone_count),
            "cluster_bonus": cluster_bonus,
        },
        veto_triggered=veto,
        veto_reason=veto_reason,
        notes=notes,
    )
    return breakdown


# =============================================================================
# 7. Layer 3: 锁定期风险分 R_lockup (越大越糟)
# =============================================================================

def _vol_risk(sigma_annualized: float) -> float:
    if sigma_annualized < 0.25:
        return 0.10
    if sigma_annualized < 0.40:
        return linear_score(sigma_annualized, 0.25, 0.40, 0.10, 0.30)
    if sigma_annualized < 0.60:
        return linear_score(sigma_annualized, 0.40, 0.60, 0.30, 0.55)
    return linear_score(sigma_annualized, 0.60, 1.00, 0.55, 0.80)


def _val_reversal_risk(pe_pct: float) -> float:
    if pe_pct < 0.50:
        return 0.10
    if pe_pct < 0.70:
        return linear_score(pe_pct, 0.50, 0.70, 0.10, 0.30)
    if pe_pct < 0.85:
        return linear_score(pe_pct, 0.70, 0.85, 0.30, 0.55)
    return linear_score(pe_pct, 0.85, 1.00, 0.55, 0.75)


def _overhang_risk(ratio: float) -> float:
    # P1-#11 重标定: overhang_ratio = pre_ipo_shares / post_ipo_shares,
    # 实际数据值域 [0.71, 1.00] (港股 IPO 老股占比典型 75%-96%).
    # 旧阈值 (0.5/1.0/2.0/4.0) 假设 ratio 可>1, 与实际数据不符 -> 区分度仅 0.11.
    # 新阈值: 0.70/0.85/0.95, 区分度跨度 0.58.
    if ratio < 0.70:
        return 0.10
    if ratio < 0.85:
        return linear_score(ratio, 0.70, 0.85, 0.10, 0.30)
    if ratio < 0.95:
        return linear_score(ratio, 0.85, 0.95, 0.30, 0.50)
    return linear_score(ratio, 0.95, 1.00, 0.50, 0.70)


def _macro_risk(hsi_pct: float) -> float:
    if hsi_pct < 0.20:
        return 0.20
    if hsi_pct < 0.50:
        return linear_score(hsi_pct, 0.20, 0.50, 0.20, 0.35)
    if hsi_pct < 0.80:
        return linear_score(hsi_pct, 0.50, 0.80, 0.35, 0.55)
    return linear_score(hsi_pct, 0.80, 1.00, 0.55, 0.75)


def score_layer3_lockup(o: IPOOffering) -> LayerBreakdown:
    assert o.market and o.lockup, "Layer 3 需要 market + lockup"
    m, lk = o.market, o.lockup

    # 6个月波动估计: max(行业, 大盘) 简化
    sigma_6m = max(m.sector_60d_vol_annualized, m.hsi_60d_vol_annualized)
    vol_r = _vol_risk(sigma_6m)
    val_r = _val_reversal_risk(lk.pe_vs_history_pct)
    over_r = _overhang_risk(lk.overhang_ratio)
    fund_r = clip(lk.fundamental_risk_score, 0.0, 1.0)
    macro_r = _macro_risk(m.hsi_valuation_pct)
    # P1-#11 重标定: peer_lockup_avg_drawdown 改用 max_drawdown_m6 均值后,
    # 实际数据 p10=0.27 / p50=0.38 / p90=0.45, 旧公式 (/0.30 满分) 让 ≥90% 样本 clip 到 1.0,
    # 完全失去区分度. 新公式: (d - 0.20) / 0.30 -> drawdown=0.20 时 0, drawdown=0.50 时满分.
    # 区分度跨度 0.95 (vs 旧 0.48).
    peer_r = clip((lk.peer_lockup_avg_drawdown - 0.20) / 0.30, 0.0, 1.0)

    cfg = _get_cfg()
    w3 = cfg.layer3_weights if cfg is not None else None
    if w3 is not None:
        R = (w3.vol * vol_r + w3.val_reversal * val_r + w3.overhang * over_r
             + w3.fundamental * fund_r + w3.macro * macro_r + w3.peer * peer_r)
    else:
        R = (0.30 * vol_r + 0.20 * val_r + 0.15 * over_r
             + 0.15 * fund_r + 0.10 * macro_r + 0.10 * peer_r)
    R = clip(R, 0.0, 1.0)

    return LayerBreakdown(
        name="R_lockup",
        raw_score=R * 100.0,
        normalized=R,
        components={
            "vol_risk": vol_r, "val_reversal_risk": val_r,
            "overhang_risk": over_r, "fundamental_risk": fund_r,
            "macro_risk": macro_r, "peer_lockup_risk": peer_r,
            "sigma_6m_proxy": sigma_6m,
        },
    )


# =============================================================================
# 8. NACS 主聚合 + 调整 + 仓位映射
# =============================================================================

# ---------------------------------------------------------------------------
# v7: Regime gate + Cluster bonus
# ---------------------------------------------------------------------------
# 这两个常量保留作为模块级符号 (被 check_health.py / 第三方代码引用),
# 但实际取值改为从 config.get_config() 动态读取, 以便 A/B 测试.
#
# 兼容策略: 默认 config 与下列原值完全一致, 不加载 YAML 时行为不变.
#
# 数据支撑:
#   - regime_score>=0 时主板 NACS_ic 60d 从 +0.09 → +0.245, t 从 0.26 → 2.41 ✅
#   - cluster≥2 IPO 60d mean +22%, std 40% (vs 无关联 mean +14%, std 68%)
REGIME_GATE_THRESHOLD = 0.0
CLUSTER_BONUS_TABLE = [
    (5, 1.20),
    (3, 1.15),
    (2, 1.10),
]


def _get_cfg():
    """lazy 导入 config, 避免循环依赖 / 启动开销"""
    try:
        from config import get_config
        return get_config()
    except ImportError:
        return None


def _cluster_bonus_multiplier(cluster_count: int) -> float:
    """v7: 根据簇基石数量返回 Q_e 加成系数 (从 config 读取)

    P3-#3: cluster bonus 实证反向预测, 强制禁用.
        数据 (2024-2025 全样本):
            cc=0   n=346 m6 mean=+11.3% (基线)
            cc=2   n=18  m6 mean=-19.1%  (×1.10 加成 → 变差 -30pp)
            cc=3-4 n=7   m6 mean=-17.6%  (×1.15 加成 → 变差 -29pp)
            cc≥5   n=3   m6 mean=-16.0%  (×1.20 加成 → 变差 -27pp)
        d30 短期偶有正 (基石锁定保护), 但 NACS 预测目标在 m3-m6,
        cluster 反映"扎堆解禁压力"反向选择, 加成帮倒忙. 强制 1.0 覆盖 config.
    """
    return 1.0


def compute_regime_score(historical_ipos, current_date,
                         lookback_days: Optional[int] = None,
                         min_lag: Optional[int] = None) -> Optional[float]:
    """
    v7 Regime detector: 给定参考日期, 计算过去 [d-lookback, d-min_lag] 上市的
    IPO 30 日收益中位数.

    Args:
        historical_ipos: List[Tuple[listing_date, return_d30]]
        current_date: 评分参考日 (通常用 pricing_date)
        lookback_days: 回看窗口 (None 则读 config, 默认 120)
        min_lag: 滞后避免用未来数据 (None 则读 config, 默认 30)

    Returns:
        regime_score (中位数) 或 None (样本不足)
    """
    from datetime import timedelta
    cfg = _get_cfg()
    if lookback_days is None:
        lookback_days = cfg.regime_gate.lookback_days if cfg is not None else 120
    if min_lag is None:
        min_lag = cfg.regime_gate.min_lag_days if cfg is not None else 30
    min_sample = cfg.regime_gate.min_sample if cfg is not None else 5

    cutoff_old = current_date - timedelta(days=lookback_days)
    cutoff_recent = current_date - timedelta(days=min_lag)
    valid = [r for d, r in historical_ipos
             if d is not None and r is not None
             and cutoff_old <= d <= cutoff_recent]
    if len(valid) < min_sample:
        return None
    return float(statistics.median(valid))


# v8 hardcoded fallback (用于 config 未加载时)
_DEFAULT_POSITION_BANDS: List[Tuple[float, float, str]] = [
    (0.55, 1.00, "FULL"),
    (0.45, 0.70, "LARGE"),
    (0.35, 0.40, "TRIAL"),
    (0.25, 0.00, "RELATIONSHIP"),  # v8: 仓位归零保留诊断标签
    (0.00, 0.00, "SKIP"),
]


def _position_from_nacs(nacs: float) -> Tuple[float, str]:
    """根据 NACS 返回 (仓位, 决策标签); 从 config.position_bands 读取档位"""
    cfg = _get_cfg()
    bands = ([(b.min_nacs, b.position, b.decision) for b in cfg.position_bands]
             if cfg is not None else _DEFAULT_POSITION_BANDS)
    for min_nacs, pos, dec in bands:
        if nacs >= min_nacs:
            return pos, dec
    # 兜底 (理论不应到达, 因最后一档 min_nacs=0)
    return 0.0, "SKIP"


def compute_nacs(o: IPOOffering) -> NACSResult:
    l1 = score_layer1_company(o)
    l2 = score_layer2_ecosystem(o)
    l3 = score_layer3_lockup(o)

    Qc, Qe, Rl = l1.normalized, l2.normalized, l3.normalized
    nacs_raw = Qc * Qe * (1 - Rl)

    cfg = _get_cfg()
    adj = cfg.post_adjustments if cfg is not None else None
    # 兜底硬编码 (与原 v8 完全一致)
    m_18c = adj.chapter_18c if adj else 0.70
    m_ah = adj.a_plus_h_short_borrowable if adj else 1.10
    m_sec = adj.secondary_listing if adj else 0.85
    m_rpt = adj.related_party_tx_recent if adj else 0.85

    adjustments: List[str] = []
    nacs_adj = nacs_raw

    # 18C 折扣
    if o.listing_chapter in (ListingChapter.CHAPTER_18C_COMMERCIAL,
                             ListingChapter.CHAPTER_18C_PRECOMMERCIAL):
        nacs_adj *= m_18c
        adjustments.append(f"18C 折扣 x{m_18c}")

    # P3-#1 已撤销: 18A 章节折扣 (×0.85) 实证 ROI 为负.
    # 9606.HK (NACS 0.404 m3=+46% m6=+70%) 被简单折扣误杀降级,
    # 净 m6 仓位贡献变差 -8.25%. 18A 内 NACS 区分度低,
    # 简单 chapter multiplier 不可行; 章节基础分=65 已经反映风险.

    # A+H 可融券对冲
    if o.is_a_h and o.a_share_short_borrowable:
        nacs_adj *= m_ah
        adjustments.append(f"A+H 同名A股可融券 x{m_ah}")

    # 第二上市
    if o.listing_chapter == ListingChapter.SECONDARY_LISTING:
        nacs_adj *= m_sec
        adjustments.append(f"第二上市 x{m_sec}")

    # 控股股东最近12月有重大关联交易
    if o.has_related_party_tx_recent:
        nacs_adj *= m_rpt
        adjustments.append(f"控股股东近12月重大关联交易 x{m_rpt}")

    # P0.2: AI 镀金检测 (theme ∈ AI_THEMES + ai_revenue_pct < threshold → ×multiplier)
    ag = adj.ai_gilding if adj is not None else None
    if (ag is not None and getattr(ag, "enabled", True)
            and o.theme_id in (ag.ai_themes or [])
            and o.ai_revenue_pct is not None
            and o.ai_revenue_pct < ag.threshold):
        nacs_adj *= ag.multiplier
        adjustments.append(
            f"AI 镀金折扣 x{ag.multiplier} "
            f"(theme={o.theme_id}, AI 收入 {o.ai_revenue_pct:.0%} < {ag.threshold:.0%})"
        )

    nacs_adj = clip(nacs_adj, 0.0, 1.0)
    pos, decision = _position_from_nacs(nacs_adj)

    # P2-#3 / P3-#2 章节差异化 RELATIONSHIP 仓位激活:
    #   实证 (2024-2025 回测):
    #     18c_commercial RELATIONSHIP n=4 60d mean=+260.6% median=+178.3% win=67%
    #     a_plus_h       RELATIONSHIP n=5 m3=+20.4% m6=+44.0% m12=+86.6%
    #                                     (n=4 m6: 3 赢 1 输, 去掉头部仍 +17%)
    #     main_board     RELATIONSHIP n=12 60d mean=+15.6% m6=-13.7% (混合负, 不激活)
    #     18a            RELATIONSHIP n=1 数据不足
    #   章节门: 18C / secondary / a_plus_h 激活 0.10, 主板/18A 维持 0
    if decision == "RELATIONSHIP" and o.listing_chapter in (
        ListingChapter.SECONDARY_LISTING,
        ListingChapter.CHAPTER_18C_COMMERCIAL,
        ListingChapter.CHAPTER_18C_PRECOMMERCIAL,
        ListingChapter.A_PLUS_H,
    ):
        pos = 0.10
        adjustments.append(
            f"P3-#2: {o.listing_chapter.value} RELATIONSHIP 仓位激活 0.10"
        )

    warnings: List[str] = []

    # ---- v7: Regime gate (在所有计算后, 决策前应用) ----
    # P2-#2 章节差异化阈值:
    #   secondary / 18C: gate 主要捕捉的是"主板 IPO 30d 中位数",
    #     与 18C/SPO 章节相关性弱; 实证显示 NACS>=0.25 的 18C 标的中
    #     仅 1/4 通过 gate=0, 而豁免后 60d mean+260%. 故放宽到 -0.10.
    base_threshold = (cfg.regime_gate.threshold if cfg is not None
                      else REGIME_GATE_THRESHOLD)
    if o.listing_chapter in (ListingChapter.SECONDARY_LISTING,
                             ListingChapter.CHAPTER_18C_COMMERCIAL,
                             ListingChapter.CHAPTER_18C_PRECOMMERCIAL):
        regime_threshold = min(base_threshold, -0.10)
    else:
        regime_threshold = base_threshold
    regime_blocked = False
    if o.regime_score is not None and o.regime_score < regime_threshold:
        regime_blocked = True
        original_decision = decision
        decision = "SKIP"
        pos = 0.0
        adjustments.append(
            f"regime_gate: SKIP (score={o.regime_score:+.4f}<{regime_threshold}, "
            f"原决策={original_decision})"
        )
        warnings.append(
            f"⚠ 制度门控阻断: 过去 90 天港股 IPO 表现弱 "
            f"(regime_score={o.regime_score:+.4f}), 强制 SKIP"
        )

    if l1.veto_triggered:
        warnings.append(f"L1 否决: {l1.veto_reason}")
    if l2.veto_triggered:
        warnings.append(f"L2 否决: {l2.veto_reason}")
    warnings.extend(l1.notes)
    warnings.extend(l2.notes)
    warnings.extend(l3.notes)

    # ===== Rationale (IC memo 用; 本身不影响打分) =====
    try:
        from nacs_rationale import (
            explain_layer1_components, explain_layer2_components,
            explain_layer3_components, explain_l1_veto, explain_l2_veto,
            explain_decision_band, explain_formula,
        )
        l1.reasons = explain_layer1_components(o, l1.components)
        if l1.veto_triggered:
            l1.reasons["_veto"] = explain_l1_veto(l1.veto_reason) or l1.veto_reason
        l2.reasons = explain_layer2_components(o, l2.components)
        if l2.veto_triggered:
            l2.reasons["_veto"] = explain_l2_veto(l2.veto_reason) or l2.veto_reason
        l3.reasons = explain_layer3_components(o, l3.components)
        decision_rationale = explain_formula(Qc, Qe, Rl, nacs_raw, nacs_adj, adjustments)
        decision_rationale.append("")
        decision_rationale.append(explain_decision_band(nacs_adj, pos, decision))
        if regime_blocked:
            decision_rationale.append(
                f"⚠ Regime gate 阻断: regime_score={o.regime_score:+.4f} "
                f"< {regime_threshold} → 强制 SKIP (覆盖原决策)"
            )
    except Exception as e:
        # rationale 渲染失败不应影响主打分; 记到 warnings
        warnings.append(f"rationale 渲染失败: {type(e).__name__}: {e}")
        decision_rationale = []

    return NACSResult(
        company_name=o.company_name,
        stock_code=o.stock_code,
        Q_company=Qc, Q_ecosystem=Qe, R_lockup=Rl,
        nacs_raw=nacs_raw, nacs_adjusted=nacs_adj,
        position_pct=pos, decision=decision,
        layer1=l1, layer2=l2, layer3=l3,
        adjustments_applied=adjustments,
        decision_rationale=decision_rationale,
        warnings=warnings,
    )


# =============================================================================
# 9. 报告输出辅助
# =============================================================================

def format_report(result: NACSResult) -> str:
    """打印 human-readable 报告"""
    lines = []
    lines.append(f"{'='*72}")
    lines.append(f"  NACS Report: {result.company_name} ({result.stock_code})")
    lines.append(f"{'='*72}")
    lines.append(f"  Q_company   : {result.Q_company:.4f}  (原始 {result.layer1.raw_score:.1f}/100)")
    lines.append(f"  Q_ecosystem : {result.Q_ecosystem:.4f}  (原始 {result.layer2.raw_score:.1f}/100)")
    lines.append(f"  R_lockup    : {result.R_lockup:.4f}  (风险, 越大越糟)")
    lines.append(f"  --------")
    lines.append(f"  NACS raw    : {result.nacs_raw:.4f}")
    lines.append(f"  NACS adj    : {result.nacs_adjusted:.4f}")
    if result.adjustments_applied:
        for a in result.adjustments_applied:
            lines.append(f"    - {a}")
    lines.append(f"  仓位建议    : {result.position_pct:.0%}  [{result.decision}]")

    if result.warnings:
        lines.append("")
        lines.append("  ⚠ Warnings / Vetoes:")
        for w in result.warnings:
            lines.append(f"    - {w}")

    lines.append("")
    lines.append("  L1 子项打分:")
    for k, v in result.layer1.components.items():
        if not k.startswith("_"):
            lines.append(f"    {k:30s} {v:8.2f}")
    lines.append("")
    lines.append("  L2 关键指标:")
    for k in ["Q_weighted", "coverage", "hhi", "diversity_entropy",
             "affiliation_pct", "chinese_pct", "longterm_pct",
             "n_cornerstones", "max_single_share"]:
        v = result.layer2.components.get(k)
        if v is not None:
            lines.append(f"    {k:30s} {v:8.4f}")
    lines.append("")
    lines.append("  L3 风险构成:")
    for k, v in result.layer3.components.items():
        lines.append(f"    {k:30s} {v:8.4f}")
    lines.append(f"{'='*72}")
    return "\n".join(lines)
