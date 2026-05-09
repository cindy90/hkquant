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
    """
    threshold: float = 0.0
    lookback_days: int = 120
    min_lag_days: int = 30
    min_sample: int = 5


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
class PostAdjustments:
    """compute_nacs 后处理乘子链"""
    chapter_18c: float = 0.70
    a_plus_h_short_borrowable: float = 1.10
    secondary_listing: float = 0.85
    related_party_tx_recent: float = 0.85


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
            kwargs["post_adjustments"] = PostAdjustments(**data["post_adjustments"])
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
