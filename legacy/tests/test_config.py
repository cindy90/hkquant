"""
NacsConfig 加载/校验/默认值 测试

覆盖:
    - 默认配置等价于原硬编码 (回归)
    - YAML 加载并通过 validate
    - 权重和必须 = 1.0
    - position_bands 必须按 min_nacs 降序
    - cluster_bonus 必须按 threshold 降序
    - get/set/reset 单例行为
"""
from __future__ import annotations

import pytest


def test_default_config_validates():
    from config import NacsConfig
    cfg = NacsConfig()
    assert cfg.validate() == []


def test_default_v8_values_match_hardcoded():
    """默认配置必须与原 v8 硬编码完全一致 (防回归)"""
    from config import NacsConfig
    cfg = NacsConfig()
    assert cfg.version == "v8"
    assert cfg.regime_gate.threshold == 0.0
    assert cfg.regime_gate.lookback_days == 120
    assert cfg.regime_gate.min_lag_days == 30

    # cluster bonus 表
    assert [(e.threshold, e.multiplier) for e in cfg.cluster_bonus] == [
        (5, 1.20), (3, 1.15), (2, 1.10),
    ]

    # v8 position bands: RELATIONSHIP 仓位 = 0
    bands = {b.decision: (b.min_nacs, b.position) for b in cfg.position_bands}
    assert bands["FULL"] == (0.55, 1.00)
    assert bands["LARGE"] == (0.45, 0.70)
    assert bands["TRIAL"] == (0.35, 0.40)
    assert bands["RELATIONSHIP"] == (0.25, 0.00)  # ★ v8 关键
    assert bands["SKIP"] == (0.00, 0.00)


def test_layer_weights_sum_to_one():
    from dataclasses import asdict
    from config import NacsConfig
    cfg = NacsConfig()
    for w in (cfg.layer1_weights, cfg.layer2_weights, cfg.layer3_weights):
        assert abs(sum(asdict(w).values()) - 1.0) < 1e-6


def test_yaml_v8_loads_and_matches_default(configs_dir):
    """configs/nacs_v8.yaml 加载结果必须与默认 NacsConfig() 等价"""
    from config import load_config, NacsConfig
    cfg = load_config(configs_dir / "nacs_v8.yaml")
    default = NacsConfig()
    assert cfg.to_dict() == default.to_dict()


def test_validate_catches_bad_weights():
    from config import NacsConfig, Layer1Weights
    cfg = NacsConfig()
    cfg.layer1_weights = Layer1Weights(
        valuation=0.5, sponsor=0.5, fundamentals=0.5,
        offering=0.0, chapter=0.0, market=0.0,
    )  # 求和=1.5, 应被检出
    errs = cfg.validate()
    assert any("layer1_weights" in e for e in errs)


def test_validate_catches_unsorted_bands():
    from config import NacsConfig, PositionBand
    cfg = NacsConfig()
    cfg.position_bands = [
        PositionBand(0.30, 0.40, "TRIAL"),
        PositionBand(0.50, 0.70, "LARGE"),  # 升序, 应被检出
    ]
    errs = cfg.validate()
    assert any("position_bands" in e for e in errs)


def test_set_get_reset_singleton():
    from config import get_config, set_config, reset_config, NacsConfig
    reset_config()
    cfg1 = get_config()
    assert cfg1.version == "v8"

    new = NacsConfig(version="test_v9")
    set_config(new)
    assert get_config().version == "test_v9"

    reset_config()
    assert get_config().version == "v8"


def test_set_config_rejects_invalid():
    from config import set_config, NacsConfig, Layer2Weights
    bad = NacsConfig()
    bad.layer2_weights = Layer2Weights(
        q_weighted=0.0, coverage=0.0, hhi=0.0, diversity=0.0,
        pollution=0.0, synergy=0.0, zucou=0.0,
    )  # 求和=0
    with pytest.raises(ValueError):
        set_config(bad)
