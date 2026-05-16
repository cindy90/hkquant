"""Tests for orchestrator/states.py — reducer behaviour."""

from __future__ import annotations

from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.orchestrator.states import _merge_extras


def test_merge_extras_keeps_left_when_right_none() -> None:
    left = WorkflowExtras(regime_score=0.10)
    right = WorkflowExtras()  # all None
    merged = _merge_extras(left, right)
    assert merged.regime_score == 0.10


def test_merge_extras_right_overrides_when_set() -> None:
    left = WorkflowExtras(regime_score=0.10)
    right = WorkflowExtras(regime_score=-0.05)
    merged = _merge_extras(left, right)
    assert merged.regime_score == -0.05


def test_merge_extras_misc_dicts_combine() -> None:
    left = WorkflowExtras(misc={"a": 1})
    right = WorkflowExtras(misc={"b": 2})
    merged = _merge_extras(left, right)
    assert merged.misc == {"a": 1, "b": 2}


def test_merge_extras_misc_right_overrides_left_on_collision() -> None:
    left = WorkflowExtras(misc={"x": 1})
    right = WorkflowExtras(misc={"x": 9})
    merged = _merge_extras(left, right)
    assert merged.misc["x"] == 9


def test_merge_extras_preserves_non_overridden_left_fields() -> None:
    left = WorkflowExtras(regime_score=0.10, cluster_bonus_multiplier=1.20)
    right = WorkflowExtras(theme_heat=0.65)
    merged = _merge_extras(left, right)
    assert merged.regime_score == 0.10
    assert merged.cluster_bonus_multiplier == 1.20
    assert merged.theme_heat == 0.65


def test_merge_extras_empty_list_does_not_override() -> None:
    left = WorkflowExtras(cluster_groups=[{"ultimate_holder": "X", "members": ["a"], "count": 2}])
    right = WorkflowExtras(cluster_groups=[])  # empty falsy → keep left
    merged = _merge_extras(left, right)
    assert len(merged.cluster_groups) == 1
