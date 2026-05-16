"""IPO lifecycle state machine per PROJECT_SPEC.md §3.11.1 + ADR 0012 §7.5c.

The state machine is the system's autonomous-operation backbone: each
IPO snapshot is attached to a lifecycle row whose state moves through
PRE_LISTING → PRICING → LISTED (3-way validated) → TERMINATED, plus
terminal branches WITHDRAWN / HEARING_FAILED / PRICING_PULLED. The
daily scheduler in 7.5d drives transitions via these modules.
"""

from .ah_special import AHContext, AHSpecialHandler
from .stale_detector import (
    PRE_LISTING_STALE_DAYS,
    PRICING_STALE_DAYS,
    StaleDetector,
    StaleSignal,
    days_in_state,
)
from .state_detectors import StateDetectors, ThreeWayValidation, TransitionSignal
from .state_machine import StateMachine, StateMachineError
from .states import (
    assert_valid_transition,
    can_transition,
    initial_state,
    is_terminal,
)
from .terminal_handlers import TERMINAL_CHECKPOINT_DAY, TerminalHandler, TerminalResult

__all__ = (
    "PRE_LISTING_STALE_DAYS",
    "PRICING_STALE_DAYS",
    "TERMINAL_CHECKPOINT_DAY",
    "AHContext",
    "AHSpecialHandler",
    "StaleDetector",
    "StaleSignal",
    "StateDetectors",
    "StateMachine",
    "StateMachineError",
    "TerminalHandler",
    "TerminalResult",
    "ThreeWayValidation",
    "TransitionSignal",
    "assert_valid_transition",
    "can_transition",
    "days_in_state",
    "initial_state",
    "is_terminal",
)
