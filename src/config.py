"""
NACS 模型参数化配置

设计目标:
    把散落在 nacs_model.py 各处的硬编码阈值/权重/乘子集中到一个 dataclass,
    支持从 YAML 加载, 让 A/B 测试无需修改代码.

向后兼容:
    - 不加载 YAML 时, 默认值与原 v8 硬编码完全一致 (回测结果可复现)
    - 通过模块级 _CONFIG 单例 + get_config()/set_config() 暴露
    - nacs_model.py 在使用时 lazy 读取, 老代码不传 config 也能跑

典型用法:
    from config import load_config, set_config
    cfg = load_config("configs/nacs_v8.yaml")
    set_config(cfg)
    # ... 后续 compute_nacs 调用都会用新 config
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any


# =============================================================================
# 子配置 dataclass
# =============================================================================

@dataclass
class RegimeGateConfig:
    """v7 制度门控: regime_score < threshold 强制 SKIP

    数据支撑: regime>=0 子样本主板 60d IC 从 +0.09→+0.245, t 从 0.26→2.41

    P2.3: per_theme_enabled=True 时, 计算 regime 用同主题 listed IPO 的中位
    (替代 panel 全量), 落到主题 cohort 的 d30. 同主题样本 < theme_min_sample
    时 fallback_to_panel=True 自动回 panel 全量 (避免单主题过拟合).
    """
    threshold: float = 0.0
    lookback_days: int = 120
    min_lag_days: int = 30
    min_sample: int = 5
    # P2.3
    per_theme_enabled: bool = False           # 默认 False (重大行为变化, 需要数据准备)
    per_theme_min_sample: int = 5             # 同主题 IPO 样本 < 此值 fallback
    fallback_to_panel: bool = True            # fallback 到 panel 全量


@dataclass
class ClusterBonusEntry:
    """簇基石加成: 同 ultimate_holder ≥threshold 个时, Q_ecosystem ×multiplier"""
    threshold: int
    multiplier: float


@dataclass
class PositionBand:
    """NACS → 仓位决策映射

    v8 改动: RELATIONSHIP 档位 (0.25-0.35) 仓位从 15% → 0%, 保留诊断标签
    """
    min_nacs: float
    position: float
    decision: str


@dataclass
class Layer1Weights:
    """L1 (Q_company) 6 子项权重, 求和应 = 1.0"""
    valuation: float = 0.25
    sponsor: float = 0.15
    fundamentals: float = 0.25
    offering: float = 0.15
    chapter: float = 0.10
    market: float = 0.10


@dataclass
class Layer2Weights:
    """L2 (Q_ecosystem) 7 子项权重, 求和应 = 1.0"""
    q_weighted: float = 0.30
    coverage: float = 0.15
    hhi: float = 0.10
    diversity: float = 0.10
    pollution: float = 0.20
    synergy: float = 0.10
    zucou: float = 0.05


@dataclass
class Layer3Weights:
    """L3 (R_lockup) 6 子项权重, 求和应 = 1.0"""
    vol: float = 0.30
    val_reversal: float = 0.20
    overhang: float = 0.15
    fundamental: float = 0.15
    macro: float = 0.10
    peer: float = 0.10


@dataclass
class Layer1Vetoes:
    """L1 否决条款: 命中则 raw 分被压顶到 veto_score_cap"""
    intl_oversub_min: float = 1.5
    last_round_premium_max: float = 0.50
    auditor_tier_max_allowed: int = 2
    veto_score_cap: float = 40.0


@dataclass
class Layer2Vetoes:
    """L2 否决条款"""
    affiliation_pct_max: float = 0.50
    affiliation_score_cap: float = 30.0
    q_weighted_min: float = 30.0
    q_weighted_score_cap: float = 40.0
    small_cs_count_threshold: int = 3
    small_cs_max_share: float = 0.60
    small_cs_score_cap: float = 35.0


@dataclass
class AiGildingAdjustment:
    """P0.2: AI 镀金 post-adjustment.

    deal 同时满足:
      - theme_id ∈ ai_themes (AI 主题集合)
      - ai_revenue_pct < threshold (实际 AI 业务收入占比低)
    → NACS_adj × multiplier (默认 0.85, 跟其它折扣一致)

    设计目的: 防止"贴 AI 标签但实际 AI 业务很小"的镀金 IPO 拿过高仓位.
    跟 nacs_checklist v3 的 VIII 区"AI 镀金检测器"对齐.
    """
    enabled: bool = True
    ai_themes: List[str] = field(default_factory=lambda: [
        "ai_server", "llm", "ai_application",
        "humanoid_robot", "ai_driving", "semi_localization",
    ])
    threshold: float = 0.10                 # AI 收入占比 < 10% 算镀金
    multiplier: float = 0.85


@dataclass
class SmallCapCSRescueFlag:
    """P3.1: 小盘 + 高基石覆盖率 = 红旗 post-adjustment.

    经验信号: 小盘公司 (offering_size 小 / 总市值小) + cornerstones 覆盖率
    异常高 (e.g. >55%) → 通常是基本面弱, 找了一篮子关联基石"救场"凑发行.
    历史 d30/60d 此类组合显著负偏.

    触发条件 (两条都满足):
        offering_size_hkd < small_offering_threshold (默认 1.5B HKD)
        cornerstone_coverage > coverage_threshold (默认 0.55)

    设计原则:
        - 仅适用于"双信号"同时, 任一不满足都不触发 (避免误伤)
        - 数据缺失保守不触发: offering_size=None 或 cornerstones 空 → 跳过
        - cfg.enabled=False → 整段跳过
    """
    enabled: bool = True
    small_offering_threshold_hkd: float = 1.5e9
    coverage_threshold: float = 0.55
    multiplier: float = 0.90


@dataclass
class AHHedgeMultiplier:
    """P2.1: A+H 同名 A 股可融券对冲 — 按 A 股 ADV (日均成交额, CNY) 分档.

    旧逻辑: is_a_h AND a_share_short_borrowable → 一律 ×1.10 (静态).
    问题: A 股 ADV 1B CNY 跟 30M CNY 的对冲成本/可行性差很多, 一刀切高估了
    冷门 A+H (如 1357.HK 美图同名 A 股流动性低, 卖空成本高) 的对冲收益.

    新逻辑 (3 档):
        adv >= high_threshold → ×high_multiplier (默认 1.10, 对冲畅通)
        adv >= mid_threshold  → ×mid_multiplier (默认 1.05, 部分对冲)
        adv <  mid_threshold  → ×low_multiplier (默认 1.00, 对冲成本拉满)

    向后兼容: a_share_adv_cny=None (历史 deal 无此字段) → 走 fallback_multiplier
    (默认 1.10, 即旧静态行为). enabled=False → 整段跳过.
    """
    enabled: bool = True
    high_threshold_cny: float = 2e8       # 200M CNY ADV
    high_multiplier: float = 1.10
    mid_threshold_cny: float = 5e7        # 50M CNY ADV
    mid_multiplier: float = 1.05
    low_multiplier: float = 1.00
    # adv 未知时的兜底 (向后兼容: 现有 IPO 不带 ADV 数据 → 沿用 1.10)
    fallback_multiplier: float = 1.10


@dataclass
class PostAdjustments:
    """compute_nacs 后处理乘子链"""
    chapter_18c: float = 0.70
    a_plus_h_short_borrowable: float = 1.10                # legacy 静态值; cfg 启用 ah_hedge 后被覆盖
    secondary_listing: float = 0.85
    related_party_tx_recent: float = 0.85
    ai_gilding: AiGildingAdjustment = field(default_factory=AiGildingAdjustment)
    ah_hedge: AHHedgeMultiplier = field(default_factory=AHHedgeMultiplier)
    small_cap_cs_rescue: SmallCapCSRescueFlag = field(
        default_factory=SmallCapCSRescueFlag
    )


@dataclass
class Layer1OfferingMktCap:
    """P1.1: 总市值分桶 modifier (作用于 L1.4 发行结构 score).

    现有 size_score 看的是 offering_size_hkd (募资额, U-shape 1.5-8B).
    本 modifier 看 mkt_cap_at_offer_hkd (总市值 = post_ipo_shares × offer_price),
    专门对极小盘和巨型盘扣分:
        市值 < small_cap_threshold (默认 5B HKD) → 流动性陷阱风险, -10
        市值 > mega_cap_threshold  (默认 500B HKD) → IPO d30 涨幅历来低, -5
        中盘 (5B-500B)             → 不动 (sweet spot)
    """
    enabled: bool = True
    small_cap_threshold_hkd: float = 5e9       # 5B HKD
    small_cap_penalty: float = -10.0
    mega_cap_threshold_hkd: float = 5e11       # 500B HKD
    mega_cap_penalty: float = -5.0


@dataclass
class Layer1ProfitabilityTier:
    """P1.2: 主板已盈利档基本面 (L1.3) 盈利质量 tier multiplier.

    在 _score_l1_3_profitable 5 子项加和后乘以一个 quality multiplier:
        persistent: roe_avg_3y >= roe_threshold AND fcf_positive_years >= fcf_min
                    → ×persistent_multiplier (默认 1.10, 持续盈利的复利型公司)
        fresh:      fcf_positive_years <= fresh_fcf_max
                    → ×fresh_multiplier (默认 0.90, 刚转盈, 现金流不稳)
        moderate:   两档都不命中 → ×1.00 不动

    设计原因: 现有 5 子项 (rev/gm/roe/nd/fcf) 各自封顶 100, 但持续 ROE>15% 跟
    刚扭亏的"假盈利"在 raw score 上可以差不多 — 加 tier 让定性差距体现到 nacs 上.

    保守: roe_avg_3y=None / fcf=0 (default) 都不触发 persistent 加成,
    fcf_positive_years=0 算 fresh.
    """
    enabled: bool = True
    persistent_roe_threshold: float = 0.15        # 3y 平均 ROE >= 此 → 候选 persistent
    persistent_fcf_min_years: int = 3             # FCF 正年数 >= 此 → 候选 persistent
    persistent_multiplier: float = 1.10
    fresh_fcf_max_years: int = 1                  # FCF 正年数 <= 此 → fresh
    fresh_multiplier: float = 0.90


@dataclass
class Layer1BiotechSubdomain:
    """P1.3: 18A 子领域 multiplier (作用于 _score_l1_3_biotech raw score).

    现有 _score_l1_3_biotech 4 子项 (phase/pipeline/runway/bd) 把所有 18A 一视同仁,
    但子领域历史 d30/60d 表现差距悬殊:
        innovative_drug (创新药): 基线; phase II → III 跨越是核心驱动
        medical_device (器械):  NMPA 审评政策依赖, 上限较低
        cell_gene (细胞基因):    Pre/I 期也有大幅升值预期, 加成
        diagnostics (诊断/IVD): 同质化竞争激烈, 估值压力大

    保守: subdomain=None / unknown → multiplier=1.0 不动 (向后兼容).
    enabled=false → 全部 ×1.0.
    """
    enabled: bool = True
    multipliers: Dict[str, float] = field(default_factory=lambda: {
        "innovative_drug": 1.00,
        "medical_device": 0.90,
        "cell_gene": 1.10,
        "diagnostics": 0.85,
    })


@dataclass
class Layer1MarketThemeHeat:
    """L1.6 主题情绪 modifier (P0.1).

    deal 在 themes/heat_today.json 里的 heat_score (0-100) 触发:
        heat ≥ overheated_threshold → L1.6 score += overheated_penalty (负值)
        heat < trough_threshold     → L1.6 score += trough_bonus       (正值)
        中间不动

    数据来源: themes/theme_tracker.py 每日 8:30 cron 跑 → heat_today.json,
    classifier 在 build_offering 时把 heat_score 注入 IPOOffering.theme_heat_score.

    禁用: enabled=false 或 heat_score=None → 跳过, 不影响打分.
    """
    enabled: bool = True
    overheated_threshold: int = 80     # heat ≥ → 罚分
    overheated_penalty: float = -5.0
    trough_threshold: int = 40         # heat < → 加分
    trough_bonus: float = 3.0


# =============================================================================
# 顶层配置
# =============================================================================

@dataclass
class NacsConfig:
    version: str = "v8"
    regime_gate: RegimeGateConfig = field(default_factory=RegimeGateConfig)
    cluster_bonus: List[ClusterBonusEntry] = field(default_factory=lambda: [
        ClusterBonusEntry(5, 1.20),
        ClusterBonusEntry(3, 1.15),
        ClusterBonusEntry(2, 1.10),
    ])
    position_bands: List[PositionBand] = field(default_factory=lambda: [
        PositionBand(0.55, 1.00, "FULL"),
        PositionBand(0.45, 0.70, "LARGE"),
        PositionBand(0.35, 0.40, "TRIAL"),
        PositionBand(0.25, 0.00, "RELATIONSHIP"),  # v8: 仓位归零保留标签
        PositionBand(0.00, 0.00, "SKIP"),
    ])
    layer1_weights: Layer1Weights = field(default_factory=Layer1Weights)
    layer2_weights: Layer2Weights = field(default_factory=Layer2Weights)
    layer3_weights: Layer3Weights = field(default_factory=Layer3Weights)
    layer1_vetoes: Layer1Vetoes = field(default_factory=Layer1Vetoes)
    layer2_vetoes: Layer2Vetoes = field(default_factory=Layer2Vetoes)
    post_adjustments: PostAdjustments = field(default_factory=PostAdjustments)
    layer1_market_theme_heat: Layer1MarketThemeHeat = field(
        default_factory=Layer1MarketThemeHeat
    )
    layer1_offering_mkt_cap: Layer1OfferingMktCap = field(
        default_factory=Layer1OfferingMktCap
    )
    layer1_profitability_tier: Layer1ProfitabilityTier = field(
        default_factory=Layer1ProfitabilityTier
    )
    layer1_biotech_subdomain: Layer1BiotechSubdomain = field(
        default_factory=Layer1BiotechSubdomain
    )

    # ---------------- I/O ----------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NacsConfig":
        """从 dict (yaml/json 解析后) 构造, 缺失字段用默认"""
        kwargs: Dict[str, Any] = {}
        if "version" in data:
            kwargs["version"] = data["version"]
        if "regime_gate" in data:
            kwargs["regime_gate"] = RegimeGateConfig(**data["regime_gate"])
        if "cluster_bonus" in data:
            kwargs["cluster_bonus"] = [
                ClusterBonusEntry(**e) for e in data["cluster_bonus"]
            ]
        if "position_bands" in data:
            kwargs["position_bands"] = [
                PositionBand(**b) for b in data["position_bands"]
            ]
        if "layer1_weights" in data:
            kwargs["layer1_weights"] = Layer1Weights(**data["layer1_weights"])
        if "layer2_weights" in data:
            kwargs["layer2_weights"] = Layer2Weights(**data["layer2_weights"])
        if "layer3_weights" in data:
            kwargs["layer3_weights"] = Layer3Weights(**data["layer3_weights"])
        if "layer1_vetoes" in data:
            kwargs["layer1_vetoes"] = Layer1Vetoes(**data["layer1_vetoes"])
        if "layer2_vetoes" in data:
            kwargs["layer2_vetoes"] = Layer2Vetoes(**data["layer2_vetoes"])
        if "post_adjustments" in data:
            pa_data = dict(data["post_adjustments"])
            ag_data = pa_data.pop("ai_gilding", None)
            ah_data = pa_data.pop("ah_hedge", None)
            sc_data = pa_data.pop("small_cap_cs_rescue", None)
            pa = PostAdjustments(**pa_data)
            if ag_data is not None:
                pa.ai_gilding = AiGildingAdjustment(**ag_data)
            if ah_data is not None:
                pa.ah_hedge = AHHedgeMultiplier(**ah_data)
            if sc_data is not None:
                pa.small_cap_cs_rescue = SmallCapCSRescueFlag(**sc_data)
            kwargs["post_adjustments"] = pa
        if "layer1_market_theme_heat" in data:
            kwargs["layer1_market_theme_heat"] = Layer1MarketThemeHeat(
                **data["layer1_market_theme_heat"]
            )
        if "layer1_offering_mkt_cap" in data:
            kwargs["layer1_offering_mkt_cap"] = Layer1OfferingMktCap(
                **data["layer1_offering_mkt_cap"]
            )
        if "layer1_profitability_tier" in data:
            kwargs["layer1_profitability_tier"] = Layer1ProfitabilityTier(
                **data["layer1_profitability_tier"]
            )
        if "layer1_biotech_subdomain" in data:
            kwargs["layer1_biotech_subdomain"] = Layer1BiotechSubdomain(
                **data["layer1_biotech_subdomain"]
            )
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """序列化, 用于实验 manifest 落盘"""
        return asdict(self)

    def validate(self) -> List[str]:
        """返回校验错误列表 (空则通过)"""
        errors: List[str] = []
        # 权重和 ≈ 1.0
        for name, w in [
            ("layer1_weights", self.layer1_weights),
            ("layer2_weights", self.layer2_weights),
            ("layer3_weights", self.layer3_weights),
        ]:
            total = sum(asdict(w).values())
            if abs(total - 1.0) > 1e-3:
                errors.append(f"{name} 求和={total:.4f}, 应=1.0")
        # 仓位档位单调
        prev_min = float("inf")
        for b in self.position_bands:
            if b.min_nacs > prev_min:
                errors.append(
                    f"position_bands 必须按 min_nacs 降序: "
                    f"{prev_min} → {b.min_nacs} ({b.decision})"
                )
            prev_min = b.min_nacs
        # cluster_bonus 阈值降序
        if self.cluster_bonus:
            prev = float("inf")
            for e in self.cluster_bonus:
                if e.threshold > prev:
                    errors.append("cluster_bonus 必须按 threshold 降序")
                prev = e.threshold
        return errors


# =============================================================================
# 加载器
# =============================================================================

def load_config(path: str | Path) -> NacsConfig:
    """从 YAML 或 JSON 文件加载.

    YAML 优先 (含注释), 失败回退 JSON.
    依赖: pyyaml (可选, 没装也能用 .json 配置)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")

    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()

    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                f"加载 {p} 需要 pyyaml. 请运行: pip install pyyaml\n"
                f"或改用 .json 后缀的配置文件"
            ) from e
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"不支持的配置文件后缀: {suffix} (用 .yaml/.yml/.json)")

    if not isinstance(data, dict):
        raise ValueError(f"配置文件根节点必须是 mapping/dict, 得到 {type(data).__name__}")

    cfg = NacsConfig.from_dict(data)
    errs = cfg.validate()
    if errs:
        raise ValueError("配置校验失败:\n  " + "\n  ".join(errs))
    return cfg


def save_config(cfg: NacsConfig, path: str | Path) -> None:
    """落盘配置 (用于实验 manifest)"""
    p = Path(path)
    suffix = p.suffix.lower()
    data = cfg.to_dict()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError("保存 YAML 需要 pyyaml") from e
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    elif suffix == ".json":
        text = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        raise ValueError(f"不支持的后缀: {suffix}")
    p.write_text(text, encoding="utf-8")


# =============================================================================
# 全局单例 (向后兼容: 老代码不传 config 也能跑)
# =============================================================================

_CONFIG: Optional[NacsConfig] = None


def get_config() -> NacsConfig:
    """获取当前全局 config; 未设置时返回默认值 (与原 v8 硬编码一致)"""
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = NacsConfig()
    return _CONFIG


def set_config(cfg: NacsConfig) -> None:
    """设置全局 config; 通常在程序入口调用一次"""
    global _CONFIG
    errs = cfg.validate()
    if errs:
        raise ValueError("set_config: 校验失败:\n  " + "\n  ".join(errs))
    _CONFIG = cfg


def reset_config() -> None:
    """重置为默认 (主要用于测试)"""
    global _CONFIG
    _CONFIG = None


if __name__ == "__main__":
    # 自检: 默认配置应通过 validate
    cfg = NacsConfig()
    errs = cfg.validate()
    if errs:
        print("默认配置校验失败:")
        for e in errs:
            print(f"  - {e}")
        raise SystemExit(1)
    print(f"OK 默认配置通过校验 (version={cfg.version})")
    print(f"  L1 权重和: {sum(asdict(cfg.layer1_weights).values()):.3f}")
    print(f"  L2 权重和: {sum(asdict(cfg.layer2_weights).values()):.3f}")
    print(f"  L3 权重和: {sum(asdict(cfg.layer3_weights).values()):.3f}")
    print(f"  仓位档位: {[b.decision for b in cfg.position_bands]}")
