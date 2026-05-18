"""R5-7 — _merge_extras shouldn't deep-copy the whole payload per reducer call.

Pre-R5-7 the reducer used ``asdict(left)`` + ``asdict(right)`` which
recursively serialised every nested list/dict. For a WorkflowExtras
holding e.g. 1,314 cornerstone_profiles entries (the real ETL size),
the reducer ran O(n) work per merge. The new field-by-field copy is
O(num_fields), independent of payload size.

These tests don't measure wall-clock (which would be flaky) — they pin
the behavioural contract that merging IS correct AND that the merged
result shares list/dict references with the input (proof of "no deep
copy").
"""

from __future__ import annotations

from hk_ipo_agent.agents.workflow_extras import WorkflowExtras
from hk_ipo_agent.orchestrator.states import _merge_extras


def test_merge_extras_right_non_none_wins() -> None:
    """R5-7 — basic last-non-None-wins semantics preserved after refactor."""
    left = WorkflowExtras(regime_score=0.1, theme_heat=0.5)
    right = WorkflowExtras(regime_score=0.2)  # theme_heat is None on right
    out = _merge_extras(left, right)
    assert out.regime_score == 0.2  # right overrides
    assert out.theme_heat == 0.5  # right None → left preserved


def test_merge_extras_empty_list_on_right_does_not_clobber_left() -> None:
    """R5-7 — empty list on right must NOT overwrite a populated left."""
    left = WorkflowExtras(cornerstone_profiles=[{"name": "A"}])
    right = WorkflowExtras()  # cornerstone_profiles defaults to []
    out = _merge_extras(left, right)
    assert out.cornerstone_profiles == [{"name": "A"}]


def test_merge_extras_misc_dict_is_merged_not_replaced() -> None:
    """R5-7 — misc dict merge semantics: right wins per-key, left keeps
    keys not touched by right."""
    left = WorkflowExtras()
    left.set("regulatory_regime", "pre-2025-08-04")
    left.set("foo", 1)
    right = WorkflowExtras()
    right.set("foo", 2)  # override
    right.set("bar", "new")  # add
    out = _merge_extras(left, right)
    assert out.misc["regulatory_regime"] == "pre-2025-08-04"  # preserved
    assert out.misc["foo"] == 2  # right wins
    assert out.misc["bar"] == "new"  # right adds


def test_merge_extras_shares_list_reference_with_left_for_large_payload() -> None:
    """R5-7 — the merged result reuses left's list reference (no deep copy).

    This is the perf-shape contract: pre-R5-7 ``asdict(left)`` recursively
    copied every list element; the new implementation does a simple
    setattr from left, so the list is the same object.
    """
    large_payload = [{"name": f"cs_{i}"} for i in range(1314)]
    left = WorkflowExtras(cornerstone_profiles=large_payload)
    right = WorkflowExtras(regime_score=0.1)  # touches different field
    out = _merge_extras(left, right)
    # SAME object identity — no deep copy happened.
    assert out.cornerstone_profiles is large_payload
    assert out.regime_score == 0.1


def test_merge_extras_returns_new_instance_not_left() -> None:
    """R5-7 — merge returns a NEW WorkflowExtras (doesn't mutate left).

    Reducers in LangGraph must be pure functions w.r.t. their inputs;
    mutating ``left`` in place would corrupt state if the same instance
    is shared across multiple merge points.
    """
    left = WorkflowExtras(regime_score=0.1)
    right = WorkflowExtras(regime_score=0.2)
    out = _merge_extras(left, right)
    assert out is not left
    assert out is not right
    # left should be unchanged.
    assert left.regime_score == 0.1
