"""R7-9 — 4 builders accept an optional ``session`` parameter in ``__init__``
so callers can compose them inside a single transaction.

Pre-R7-9 every builder method opened its own ``async_session_factory()()``
context. That meant:
  * Two builders called sequentially couldn't share a transaction.
  * A caller who already had a session (e.g. an API handler with a
    request-scoped session) couldn't reuse it.
  * Integration tests had to plumb work through global factory state.

Post-R7-9 each builder's ``__init__`` accepts ``session: AsyncSession | None
= None``. When provided, methods use it; when None, methods fall back to
opening their own factory (back-compat with existing callers).

Affected builders:
  * ComparablePoolBuilder
  * CornerstoneProfileBuilder
  * HistoricalIPOLoader
  * SponsorTrackBuilder

(ThemeLoader operates on filesystem JSON, no session.)
"""

from __future__ import annotations

import inspect

from hk_ipo_agent.data.builders import (
    ComparablePoolBuilder,
    CornerstoneProfileBuilder,
    HistoricalIPOLoader,
    SponsorTrackBuilder,
)

_BUILDERS = (
    ComparablePoolBuilder,
    CornerstoneProfileBuilder,
    HistoricalIPOLoader,
    SponsorTrackBuilder,
)


import pytest


@pytest.mark.parametrize("cls", _BUILDERS)
def test_builder_init_accepts_session_kwarg(cls: type) -> None:
    """R7-9 — every builder __init__ accepts ``session`` as a keyword.

    The kwarg is optional with default None to preserve back-compat with
    callers that don't manage their own session.
    """
    sig = inspect.signature(cls.__init__)
    params = sig.parameters
    assert "session" in params, (
        f"R7-9: {cls.__name__}.__init__ must accept a 'session' kwarg (got params={list(params)})"
    )
    # Must have a default so zero-arg construction still works.
    session_param = params["session"]
    assert session_param.default is not inspect.Parameter.empty, (
        f"R7-9: {cls.__name__}.__init__ session kwarg must have a default "
        "(None) to keep back-compat"
    )


@pytest.mark.parametrize("cls", _BUILDERS)
def test_builder_constructs_with_no_args(cls: type) -> None:
    """R7-9 — back-compat: existing call sites that do ``Builder()`` still work."""
    # We can't actually call __init__ for builders that require iFind, but
    # we can verify the signature has no REQUIRED positional args.
    sig = inspect.signature(cls.__init__)
    required = [
        p
        for p in sig.parameters.values()
        if p.name != "self"
        and p.default is inspect.Parameter.empty
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    assert not required, (
        f"R7-9: {cls.__name__}.__init__ must have no required positional args "
        f"(found {[p.name for p in required]})"
    )


@pytest.mark.parametrize("cls", _BUILDERS)
def test_builder_stashes_session_attribute(cls: type) -> None:
    """R7-9 — the injected session is stored on the instance (so methods
    can find it via ``self._session`` or ``self.session``).
    """
    src = inspect.getsource(cls.__init__)
    has_session_attr = "self._session" in src or "self.session" in src or "self.__session" in src
    assert has_session_attr, (
        f"R7-9: {cls.__name__}.__init__ must stash the session on self "
        "(self._session = session or similar)"
    )
