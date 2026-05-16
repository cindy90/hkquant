"""State helper functions per PROJECT_SPEC.md §3.11.1.

The 7 states + ``VALID_TRANSITIONS`` table live in ``common/enums.py``.
This module wraps them with predicate / helper functions used by the
state machine. CLAUDE.md v1.2: "状态机不得回退" — any transition outside
VALID_TRANSITIONS raises ``InvalidStateTransition`` (defined in
``common/exceptions.py``).
"""

from __future__ import annotations

from ...common.enums import VALID_TRANSITIONS, IPOLifecycleStateType
from ...common.exceptions import InvalidStateTransition

# Terminal states have no outgoing transitions per the enum table.
_TERMINAL = frozenset(
    {s for s, outs in VALID_TRANSITIONS.items() if not outs}
)


def is_terminal(state: IPOLifecycleStateType) -> bool:
    """True iff ``state`` is a leaf node in the state graph."""
    return state in _TERMINAL


def can_transition(
    from_state: IPOLifecycleStateType,
    to_state: IPOLifecycleStateType,
) -> bool:
    """Returns True iff (from, to) is in VALID_TRANSITIONS."""
    return to_state in VALID_TRANSITIONS.get(from_state, [])


def assert_valid_transition(
    from_state: IPOLifecycleStateType,
    to_state: IPOLifecycleStateType,
) -> None:
    """Raise ``InvalidStateTransition`` if (from, to) isn't allowed.

    Includes the human-readable allowed-targets list in the error message
    so the operator immediately sees what *is* possible.
    """
    if can_transition(from_state, to_state):
        return
    allowed = VALID_TRANSITIONS.get(from_state, [])
    raise InvalidStateTransition(
        f"Cannot transition {from_state.value} → {to_state.value}. "
        f"Allowed from {from_state.value}: "
        f"{[s.value for s in allowed] or 'terminal (no transitions)'}"
    )


def initial_state() -> IPOLifecycleStateType:
    """The state a fresh snapshot enters."""
    return IPOLifecycleStateType.PRE_LISTING


__all__ = (
    "assert_valid_transition",
    "can_transition",
    "initial_state",
    "is_terminal",
)
