"""R7-10 — async_session_factory uses ContextVar instead of lru_cache for
event-loop-safe singleton semantics.

Pre-R7-10 the factory was wrapped in ``functools.lru_cache(maxsize=1)``.
That globally binds the first call's engine to whichever event loop
created it. Subsequent calls from a DIFFERENT event loop (pytest-asyncio
per-test loops; concurrent backtests; production worker recycle) reuse
the cached engine whose ``asyncpg.Pool`` internally still references the
dead loop — symptoms range from ``RuntimeError: <Future> attached to a
different loop`` to silent connection leaks.

Post-R7-10:
  * Factory storage is a ``contextvars.ContextVar[async_sessionmaker | None]``.
  * First call within a context creates + stores the factory; later calls
    in the same context return the stored instance.
  * A new asyncio context (new event loop / fresh task) sees the ContextVar
    as None and constructs its own factory bound to its own loop.

These tests verify the implementation by source inspection (the
multi-loop behaviour requires a custom event-loop fixture and is exercised
in integration tests).
"""

from __future__ import annotations

import inspect

from hk_ipo_agent.data import database as db_mod


def test_async_session_factory_no_longer_uses_lru_cache() -> None:
    """R7-10 — the function MUST NOT be wrapped in ``@functools.lru_cache``."""
    source = inspect.getsource(db_mod.async_session_factory)
    # The decorator line appears in the source. Pre-fix it was
    # ``@functools.lru_cache(maxsize=1)``; post-fix the decorator should
    # be gone OR replaced.
    # We allow the existence of ``functools.lru_cache`` elsewhere in the
    # module (e.g. on get_engine), but NOT on async_session_factory.
    assert "lru_cache" not in source, (
        "R7-10: async_session_factory must not be lru_cache-wrapped — "
        "use ContextVar storage instead"
    )


def test_database_module_imports_contextvar() -> None:
    """R7-10 — the module imports contextvars."""
    module_source = inspect.getsource(db_mod)
    assert (
        "from contextvars import ContextVar" in module_source
        or "import contextvars" in module_source
    ), "R7-10: database module must import ContextVar"


def test_session_factory_contextvar_exists() -> None:
    """R7-10 — there's a module-level ContextVar holding the factory."""
    from contextvars import ContextVar

    # The implementation may name it anything; scan module-level names for
    # any attribute whose value is a ContextVar.
    found_ctxvar = False
    for name in dir(db_mod):
        if name.startswith("__"):
            continue
        attr = getattr(db_mod, name)
        if isinstance(attr, ContextVar):
            found_ctxvar = True
            break
    assert found_ctxvar, (
        "R7-10: database module must expose at least one ContextVar "
        "(for the per-context session factory storage)"
    )


def test_async_session_factory_still_callable() -> None:
    """R7-10 — back-compat: ``async_session_factory()`` still returns a
    session-maker callable.
    """
    factory = db_mod.async_session_factory()
    # Should be an async_sessionmaker (an instance, not the class).
    from sqlalchemy.ext.asyncio import async_sessionmaker

    assert isinstance(factory, async_sessionmaker)


def test_async_session_factory_returns_same_within_one_context() -> None:
    """R7-10 — within the same asyncio context, repeated calls return the
    SAME factory (singleton-in-context semantics).
    """
    a = db_mod.async_session_factory()
    b = db_mod.async_session_factory()
    assert a is b, (
        "R7-10: within one context, async_session_factory() must return the "
        "same factory instance on repeated calls"
    )
