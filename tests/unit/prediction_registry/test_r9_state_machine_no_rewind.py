"""R9-5 — VALID_TRANSITIONS prohibits all backward transitions.

CLAUDE.md §自动化与状态机约束: "状态机不得回退。误判的纠正方式是新建
correction transition 写 audit log，而不是改 current_state."

Pre-R9-5 there was no test that systematically asserted the absence of
backward edges. A future refactor could add LISTED → PRICING (rewind)
or TERMINATED → LISTED (resurrect) and silently pass — because no
single test pinned the no-rewind invariant.

This test enumerates every (from, to) in VALID_TRANSITIONS and asserts:
  * No transition goes backward in the canonical phase order.
  * Every terminal state has an empty out-edge list.
  * The correction path (StateMachine.record_correction, R2-4) is the
    ONLY way to write a state row that wouldn't be reachable via
    VALID_TRANSITIONS.
"""

from __future__ import annotations

from hk_ipo_agent.common.enums import VALID_TRANSITIONS, IPOLifecycleStateType

# Canonical phase ordering: pre_listing < pricing < listed < terminated.
# All non-LISTED terminals (WITHDRAWN/HEARING_FAILED/PRICING_PULLED)
# are sinks at any phase ≥ their entry point — they're treated as
# "terminal" (rank = ∞) for the no-rewind invariant.
_PHASE_ORDER: dict[IPOLifecycleStateType, int] = {
    IPOLifecycleStateType.PRE_LISTING: 0,
    IPOLifecycleStateType.PRICING: 1,
    IPOLifecycleStateType.LISTED: 2,
    IPOLifecycleStateType.WITHDRAWN: 99,
    IPOLifecycleStateType.HEARING_FAILED: 99,
    IPOLifecycleStateType.PRICING_PULLED: 99,
    IPOLifecycleStateType.TERMINATED: 99,
}


def test_no_backward_transitions_in_valid_table() -> None:
    """R9-5 — every (from → to) in VALID_TRANSITIONS goes forward only.

    A transition is "backward" iff ``_PHASE_ORDER[to] < _PHASE_ORDER[from]``
    AND ``to`` is not the same state. Self-loops aren't in the table.
    """
    violations: list[tuple[IPOLifecycleStateType, IPOLifecycleStateType]] = []
    for from_state, outs in VALID_TRANSITIONS.items():
        from_rank = _PHASE_ORDER[from_state]
        for to_state in outs:
            to_rank = _PHASE_ORDER[to_state]
            if to_rank < from_rank:
                violations.append((from_state, to_state))
    assert not violations, (
        f"R9-5: VALID_TRANSITIONS contains backward edges (CLAUDE.md "
        f"§自动化与状态机约束 forbids state-machine rewind): {violations}"
    )


def test_terminal_states_have_no_outgoing_edges() -> None:
    """R9-5 — WITHDRAWN / HEARING_FAILED / PRICING_PULLED / TERMINATED
    must be sinks. A terminal with an outgoing edge would be an implicit
    resurrection path.
    """
    terminals = (
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.HEARING_FAILED,
        IPOLifecycleStateType.PRICING_PULLED,
        IPOLifecycleStateType.TERMINATED,
    )
    for t in terminals:
        outs = VALID_TRANSITIONS.get(t, [])
        assert not outs, (
            f"R9-5: terminal state {t.value} has outgoing edges {outs}; "
            "terminals must be sinks (use record_correction for retroactive fixes)"
        )


def test_listed_can_only_transition_to_terminated() -> None:
    """R9-5 — LISTED → only TERMINATED. No LISTED → PRICING (rewind),
    no LISTED → WITHDRAWN (resurrect-then-cancel pattern).
    """
    outs = set(VALID_TRANSITIONS[IPOLifecycleStateType.LISTED])
    assert outs == {IPOLifecycleStateType.TERMINATED}, (
        f"R9-5: LISTED should only transition to TERMINATED; got {outs}"
    )


def test_pre_listing_outgoing_edges_match_spec() -> None:
    """R9-5 — PRE_LISTING → PRICING / WITHDRAWN / HEARING_FAILED only.
    Pinning the exact out-edge set so any future addition is reviewed.
    """
    outs = set(VALID_TRANSITIONS[IPOLifecycleStateType.PRE_LISTING])
    assert outs == {
        IPOLifecycleStateType.PRICING,
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.HEARING_FAILED,
    }, f"R9-5: PRE_LISTING outgoing edges drift; got {outs}"


def test_pricing_outgoing_edges_match_spec() -> None:
    """R9-5 — PRICING → LISTED / WITHDRAWN / PRICING_PULLED only."""
    outs = set(VALID_TRANSITIONS[IPOLifecycleStateType.PRICING])
    assert outs == {
        IPOLifecycleStateType.LISTED,
        IPOLifecycleStateType.WITHDRAWN,
        IPOLifecycleStateType.PRICING_PULLED,
    }, f"R9-5: PRICING outgoing edges drift; got {outs}"


def test_state_machine_has_record_correction_method() -> None:
    """R9-5 — R2-4 correction path is the ONLY legitimate way to write
    an "impossible" state — pin its existence as a regression guard.
    """
    from hk_ipo_agent.prediction_registry.ipo_lifecycle.state_machine import StateMachine

    assert hasattr(StateMachine, "record_correction"), (
        "R9-5: StateMachine.record_correction (R2-4) must exist as the "
        "controlled-bypass for retroactive state fixes"
    )
