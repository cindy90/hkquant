"""Synthesizer — Opus 4.7 aggregator that turns agents + debate into FinalDecision."""

from __future__ import annotations

from .decision_engine import DecisionGate, decide
from .price_range import derive_price_range
from .scoring import build_scorecard
from .synthesizer import synthesize
from .trigger_rules import attach_trigger_rules, build_trigger_rules
from .whatif import run_whatif

__all__ = (
    "DecisionGate",
    "attach_trigger_rules",
    "build_scorecard",
    "build_trigger_rules",
    "decide",
    "derive_price_range",
    "run_whatif",
    "synthesize",
)
