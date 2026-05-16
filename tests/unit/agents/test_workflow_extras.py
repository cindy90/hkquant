"""Tests for ``WorkflowExtras`` cross-agent shared container."""

from __future__ import annotations

import pytest

from hk_ipo_agent.agents.workflow_extras import WorkflowExtras


def test_defaults_are_none_or_empty() -> None:
    extras = WorkflowExtras()
    assert extras.regime_score is None
    assert extras.cluster_bonus_multiplier is None
    assert extras.theme_heat is None
    assert extras.ai_gilding_flag is False
    assert extras.peer_multiples == {}
    assert extras.cluster_groups == []


def test_dict_style_get_returns_typed_field_when_set() -> None:
    extras = WorkflowExtras()
    extras.regime_score = 0.123
    assert extras.get("regime_score") == 0.123


def test_dict_style_get_falls_back_to_default_for_unknown_key() -> None:
    extras = WorkflowExtras()
    assert extras.get("nonexistent", "default") == "default"


def test_set_writes_typed_field() -> None:
    extras = WorkflowExtras()
    extras.set("regime_score", -0.05)
    assert extras.regime_score == -0.05


def test_set_unknown_key_goes_to_misc() -> None:
    extras = WorkflowExtras()
    extras.set("custom_signal", {"x": 1})
    assert extras.misc["custom_signal"] == {"x": 1}
    assert extras.get("custom_signal") == {"x": 1}


def test_reserved_keys_rejected() -> None:
    extras = WorkflowExtras()
    with pytest.raises(KeyError):
        extras.set("misc", {})
