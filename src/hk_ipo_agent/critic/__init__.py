"""Critic layer: Bull-Bear-Devil debate + cross-check against historical IPOs."""

from __future__ import annotations

from .bear import run_bear
from .bull import run_bull
from .cross_checker import CrossCheckResult, cross_check
from .debate_graph import jaccard, run_debate, tokenize
from .devils_advocate import run_devils_advocate

__all__ = (
    "CrossCheckResult",
    "cross_check",
    "jaccard",
    "run_bear",
    "run_bull",
    "run_debate",
    "run_devils_advocate",
    "tokenize",
)
