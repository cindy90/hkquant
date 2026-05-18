"""R8-6 — EventDrivenScheduler accepts a registry kwarg; no global get_registry().

Pre-R8-6 ``_handle_earnings`` did:

    from ..registry import get_registry
    snap = await get_registry().get_snapshot(snapshot_id)

The global ``get_registry()`` returns whichever registry was last
``set_registry``-d in the process. In a test that runs the
event-driven scheduler with InMemoryPredictionRegistry, then the API
lifespan switches to PGPredictionRegistry, the scheduler silently
reads from PG — wrong store, possibly cross-context bugs.

Post-R8-6 the registry is injected via ``__init__(registry=...)`` and
used directly. Default ``None`` falls back to ``get_registry()`` for
back-compat with existing call sites (e.g. ``BaseScheduler`` factory
patterns that haven't been updated).
"""

from __future__ import annotations

import inspect

from hk_ipo_agent.prediction_registry.schedulers.event_driven_scheduler import (
    EventDrivenScheduler,
)


def test_init_accepts_registry_kwarg() -> None:
    """R8-6 — __init__ has a ``registry`` kwarg (keyword-only)."""
    sig = inspect.signature(EventDrivenScheduler.__init__)
    params = sig.parameters
    assert "registry" in params, (
        f"R8-6: EventDrivenScheduler.__init__ must accept ``registry`` kwarg (got {list(params)})"
    )
    # Optional with default None to preserve back-compat.
    assert params["registry"].default is None


def test_handle_earnings_uses_injected_registry_not_global() -> None:
    """R8-6 — ``_handle_earnings`` source uses ``self._registry`` (or its
    callable equivalent), not ``get_registry()`` directly.

    AST guard: walk the method body and confirm there's no call to
    ``get_registry`` — the registry must come from the injected
    attribute set in __init__.
    """
    import ast
    import textwrap

    source = inspect.getsource(EventDrivenScheduler._handle_earnings)
    tree = ast.parse(textwrap.dedent(source))
    # No direct call to ``get_registry`` allowed.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            assert name != "get_registry", (
                "R8-6: _handle_earnings must NOT call get_registry() — "
                "use self._registry (injected) instead"
            )


def test_init_stashes_registry_on_self() -> None:
    """R8-6 — __init__ writes self._registry / self.registry."""
    source = inspect.getsource(EventDrivenScheduler.__init__)
    assert "self._registry" in source or "self.registry" in source, (
        "R8-6: __init__ must stash the injected registry on self"
    )
