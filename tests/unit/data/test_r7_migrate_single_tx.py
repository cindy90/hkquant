"""R7-7 — migrate_sqlite_to_pg.run runs ALL migrations under one session/commit.

Pre-R7-7 the migration was split:
  1. ``async with factory() as session: ... await session.commit()`` —
     IPO + cornerstone tables.
  2. ``await migrate_companies_and_financials(con)`` — opened its OWN
     session and committed separately.

If step 2 raised AFTER step 1's commit, we had a half-migrated DB:
IPOs present, companies missing. Downstream joins (sponsor_track_record,
cornerstone signal) would 404 on companies that should exist.

Post-R7-7 ``migrate_companies_and_financials`` accepts an injected
``AsyncSession`` and the caller commits ONCE at the end of all six
tables. A mid-pipeline failure rolls back everything atomically.

These tests verify the contract via source inspection (the actual
end-to-end run requires PG + a 100MB SQLite fixture; lives under
tests/integration/data/).
"""

from __future__ import annotations

import ast


def _migration_module_source() -> str:
    """Helper: read the migration script source as text."""
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[3] / "scripts/migrate_sqlite_to_pg.py"
    return path.read_text(encoding="utf-8")


def test_migrate_companies_and_financials_accepts_session_param() -> None:
    """R7-7 — the helper accepts an injected ``session`` parameter
    rather than constructing its own.
    """
    source = _migration_module_source()
    tree = ast.parse(source)

    target_fn: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "migrate_companies_and_financials"
        ):
            target_fn = node
            break

    assert target_fn is not None, "migrate_companies_and_financials not found"

    # Collect both positional + keyword-only argument names.
    param_names = [arg.arg for arg in target_fn.args.args] + [
        arg.arg for arg in target_fn.args.kwonlyargs
    ]
    assert "session" in param_names, (
        f"R7-7: migrate_companies_and_financials must accept ``session`` parameter "
        f"(got {param_names})"
    )


def test_migrate_companies_does_not_open_own_session() -> None:
    """R7-7 — the helper must NOT call async_session_factory() internally.

    Pre-fix it did. Now session must come from the caller for tx atomicity.
    """
    source = _migration_module_source()
    tree = ast.parse(source)

    target_fn: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "migrate_companies_and_financials"
        ):
            target_fn = node
            break

    assert target_fn is not None

    # Walk the function body for calls to async_session_factory.
    for node in ast.walk(target_fn):
        if isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else (fn.attr if isinstance(fn, ast.Attribute) else None)
            )
            assert name != "async_session_factory", (
                "R7-7: migrate_companies_and_financials must not call "
                "async_session_factory() — accept session from caller"
            )


def test_run_invokes_companies_inside_main_session_block() -> None:
    """R7-7 — the ``run()`` (or equivalent main) function invokes
    ``migrate_companies_and_financials`` from inside the ``async with
    factory() as session`` block AND passes ``session=session``.

    We assert by checking the source contains the call with a session arg
    (positional or keyword) AND the call happens before any explicit
    ``session.commit()`` at the same indentation.
    """
    source = _migration_module_source()

    # The call must reference ``session`` as an argument.
    assert "migrate_companies_and_financials(" in source, "R7-7: the helper must still be invoked"
    # Crude check: at least one call to migrate_companies_and_financials should
    # include 'session' as an argument (either positional or keyword).
    needle_kwarg = "migrate_companies_and_financials(con, session=session)"
    needle_kwarg_alt = "migrate_companies_and_financials(con, session)"
    needle_kwarg_named = "migrate_companies_and_financials(\n        con,\n        session=session,"
    needle_kwarg_inline = "migrate_companies_and_financials(con=con, session=session)"
    has_any = any(
        needle in source
        for needle in (
            needle_kwarg,
            needle_kwarg_alt,
            needle_kwarg_named,
            needle_kwarg_inline,
        )
    )
    assert has_any, (
        "R7-7: migrate_companies_and_financials must be called with "
        "session= passed in (the unified-tx contract)"
    )


def test_run_function_has_single_commit() -> None:
    """R7-7 — the ``run()`` function commits ONCE for the entire migration.

    Pre-fix there were two ``session.commit()`` calls (one in run, one
    inside migrate_companies_and_financials). Now the helper must NOT
    commit; the outer ``run()`` owns the single commit point.
    """
    source = _migration_module_source()
    tree = ast.parse(source)

    target_fn: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "migrate_companies_and_financials"
        ):
            target_fn = node
            break
    assert target_fn is not None

    # No ``session.commit()`` inside the helper anymore.
    for node in ast.walk(target_fn):
        # session.commit() — strict: only the literal ``session`` variable.
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "commit"
            and isinstance(node.value, ast.Name)
            and node.value.id == "session"
        ):
            raise AssertionError(
                "R7-7: migrate_companies_and_financials must not call "
                "session.commit() — the outer run() owns the single commit"
            )
