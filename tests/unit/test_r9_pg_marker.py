"""R9-7 — verify ``pg_required`` marker is registered + helpers importable.

The full file-relocation (PG-required tests → tests/integration/) is
deferred as a follow-up; this test pins the lighter contract:
  * The marker is registered in pyproject.toml so ``pytest -m
    'not pg_required'`` actually filters something.
  * The shared helpers (``sync_dsn`` / ``truncate_tables`` /
    ``fresh_async_session_factory``) are importable from
    ``tests._pg_helpers``.
"""

from __future__ import annotations


def test_pg_required_marker_is_registered() -> None:
    """R9-7 — ``pg_required`` appears in ``pyproject.toml`` markers."""
    import pathlib
    import tomllib

    pyproject = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    markers = data["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("pg_required:") for m in markers), (
        f"R9-7: 'pg_required' marker missing from pyproject.toml; got {markers}"
    )


def test_pg_helpers_module_exports_three_helpers() -> None:
    """R9-7 — tests._pg_helpers exposes sync_dsn / truncate_tables /
    fresh_async_session_factory."""
    from tests import _pg_helpers

    for name in ("sync_dsn", "truncate_tables", "fresh_async_session_factory"):
        assert hasattr(_pg_helpers, name), f"R9-7: tests._pg_helpers must export {name}"


def test_pg_helpers_sync_dsn_uses_psycopg_scheme(monkeypatch) -> None:
    """R9-7 — sync_dsn strips the ``+asyncpg`` driver suffix so psycopg
    can consume the DSN."""
    from tests._pg_helpers import sync_dsn

    # We don't override settings here; just assert the output shape.
    out = sync_dsn()
    assert out.startswith("postgresql://"), (
        f"R9-7: sync_dsn should be a plain postgresql:// DSN; got {out[:30]}..."
    )
    assert "+asyncpg" not in out
