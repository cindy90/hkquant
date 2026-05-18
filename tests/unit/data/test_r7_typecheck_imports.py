"""R7-1 — TYPE_CHECKING imports in builders must resolve under static analysis.

Pre-R7-1 ``historical_ipo_loader.py`` had:

    if TYPE_CHECKING:
        from .data.sources.ifind_client import IFindClient

That's a broken relative path: the file lives at
``src/hk_ipo_agent/data/builders/historical_ipo_loader.py`` so
``.data.sources.ifind_client`` would resolve to
``hk_ipo_agent.data.builders.data.sources.ifind_client`` — which doesn't
exist. The dead code path never tripped because TYPE_CHECKING is False at
runtime, but mypy / IDE go-to-definition broke silently and any future
runtime annotation evaluation (``from __future__ import annotations`` is
on, but ``typing.get_type_hints()`` resolves them lazily) would crash.

Post-R7-1 the import is corrected to ``from ..sources.ifind_client``.
This test verifies the import resolves at runtime by stripping the
TYPE_CHECKING guard via ast.
"""

from __future__ import annotations

import ast
import inspect


def test_historical_ipo_loader_typecheck_import_resolves() -> None:
    """R7-1 — the TYPE_CHECKING import in historical_ipo_loader uses a path
    that, when executed, actually imports a real module."""
    import hk_ipo_agent.data.builders.historical_ipo_loader as mod

    tree = ast.parse(inspect.getsource(mod))

    # Walk for the TYPE_CHECKING-guarded ImportFrom that references ifind_client.
    found_ifind_import: ast.ImportFrom | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "ifind_client" in node.module:
            found_ifind_import = node
            break

    assert found_ifind_import is not None, (
        "expected a TYPE_CHECKING import of ifind_client in historical_ipo_loader"
    )

    # The level-of-dots tells us the relative depth. From
    # src/hk_ipo_agent/data/builders/historical_ipo_loader.py to
    # src/hk_ipo_agent/data/sources/ifind_client.py is "up one
    # (builders → data), then into sources" → level=2, module="sources.ifind_client".
    assert found_ifind_import.level == 2, (
        f"R7-1: relative import depth should be 2 (up from builders/ to data/, "
        f"then into sources/), got level={found_ifind_import.level}; "
        f"module={found_ifind_import.module!r}"
    )
    assert found_ifind_import.module == "sources.ifind_client", (
        f"R7-1: expected module='sources.ifind_client' under level=2, "
        f"got {found_ifind_import.module!r}"
    )


def test_historical_ipo_loader_ifind_class_resolves_at_runtime() -> None:
    """R7-1 — the IFindClient class actually exists at the imported path.

    We import the absolute path directly (not via TYPE_CHECKING) to verify
    the target exists. This catches the case where TYPE_CHECKING gates a
    bogus import that would only fail under static analysis or
    ``get_type_hints()``.
    """
    from hk_ipo_agent.data.sources.ifind_client import IFindClient

    assert IFindClient is not None
    assert callable(IFindClient)
