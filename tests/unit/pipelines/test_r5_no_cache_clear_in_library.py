"""R5-5 — ``clear_config_caches`` is a CLI-only concern, not a library concern.

Pre-R5-5 ``pipelines.pdf_to_snapshot.run_pdf_to_snapshot`` called
``clear_config_caches()`` unconditionally on every invocation. The
function nukes ``functools.lru_cache``-backed accessors on the global
``Settings`` / ``LLMModelsConfig`` etc. objects — process-wide. Two
concurrent pipeline runs would race: pipeline A starts → A clears caches →
B starts mid-run → A's ``get_settings()`` (or any agent's late call) is
forced to re-parse YAML mid-flight, and if test monkeypatching is in play
during B's setup, A may see B's overrides.

Post-R5-5 the library NEVER touches the global caches. The CLI
(``scripts/analyze_pdf.py``) is the single entry point that may legitimately
want to reset caches on startup (e.g. when a long-lived shell re-runs the
script after editing config/*.yaml). Tests still call ``clear_config_caches``
explicitly in their fixtures — that's fine because tests serialise.

These tests pin the **library** boundary; the CLI call site is verified
by inspection in ``scripts/analyze_pdf.py`` not by unit test (the CLI
parses argv, which is awkward to test without subprocess.run).
"""

from __future__ import annotations

import ast
import inspect


def test_pdf_to_snapshot_library_does_not_call_clear_config_caches() -> None:
    """R5-5 — the library function NEVER clears global caches mid-process.

    AST-level check so prose mentions in docstrings / comments don't false-fire.
    """
    import hk_ipo_agent.pipelines.pdf_to_snapshot as pdf_mod

    tree = ast.parse(inspect.getsource(pdf_mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            assert name != "clear_config_caches", (
                "pipelines.pdf_to_snapshot still calls clear_config_caches() — "
                "R5-5 moves this to the CLI entry point (scripts/analyze_pdf.py)."
            )


def test_pdf_to_snapshot_does_not_import_clear_config_caches() -> None:
    """R5-5 — the import itself is also gone; nothing in the library reaches
    for cache-clearing helpers."""
    import hk_ipo_agent.pipelines.pdf_to_snapshot as pdf_mod

    tree = ast.parse(inspect.getsource(pdf_mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert all(alias.name != "clear_config_caches" for alias in node.names), (
                "pipelines.pdf_to_snapshot still imports clear_config_caches"
            )
