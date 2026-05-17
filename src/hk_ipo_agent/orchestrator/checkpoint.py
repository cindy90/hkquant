"""Checkpoint provider (Phase 6 stub).

PROJECT_SPEC.md §3.8 requires PostgresSaver in production. Phase 6 ships
``MemorySaver`` (in-process) so the LangGraph compile call succeeds in
unit tests; Phase 7 will swap to ``langgraph.checkpoint.postgres.PostgresSaver``.

Keeping the API stable means callers in ``graph.py`` only need to call
``get_checkpointer()`` and don't care about the backing store.
"""

from __future__ import annotations

from typing import Any


def get_checkpointer() -> Any | None:
    """Return a LangGraph checkpointer instance, or None if unavailable.

    Tries ``MemorySaver`` first (always available with langgraph>=0.2).
    On import failure returns None — caller compiles the graph without
    persistence (acceptable for unit-test usage).
    """
    try:
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError:
        return None
    return MemorySaver()


__all__ = ("get_checkpointer",)
