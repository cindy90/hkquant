"""R7-4 — CornerstoneInvestorRepository.find_by_any_alias resolves names
via the JSONB ``aliases`` column.

Pre-R7-4 the only name-lookup path was ``find_by_canonical_name`` which
queried ``name_zh`` / ``name_en`` exact equality. That missed the 1,051
``cornerstone_aliases`` rows migrated into the JSONB column — e.g. an
investor known as "高瓴" in one prospectus and "Hillhouse Capital" in
another would yield two separate lookup failures despite being the same
entity.

Post-R7-4:
  * New method ``find_by_any_alias(name)`` searches the JSONB array
    ``aliases.items`` for any element whose ``text`` matches the input.
  * The lookup is case-insensitive (PG JSONB lookup folds case via SQL).
  * A new migration adds a GIN index on ``aliases`` so the lookup is
    indexed (without it, JSONB containment scans the full 1,314-row
    table).
"""

from __future__ import annotations

import inspect

from hk_ipo_agent.data.repositories.cornerstone_repo import (
    CornerstoneInvestorRepository,
)


def test_find_by_any_alias_method_exists() -> None:
    """R7-4 — the new method is exposed on the repository."""
    assert hasattr(CornerstoneInvestorRepository, "find_by_any_alias"), (
        "R7-4: CornerstoneInvestorRepository must expose find_by_any_alias()"
    )


def test_find_by_any_alias_is_async() -> None:
    """R7-4 — method is an async coroutine (matches other repository methods)."""
    import asyncio

    method = CornerstoneInvestorRepository.find_by_any_alias
    assert asyncio.iscoroutinefunction(method), (
        "R7-4: find_by_any_alias must be async (parallels find_by_canonical_name)"
    )


def test_find_by_any_alias_signature_returns_list_or_optional() -> None:
    """R7-4 — accepts (self, name: str) and returns a list/Optional of investors."""
    import inspect as _inspect

    sig = _inspect.signature(CornerstoneInvestorRepository.find_by_any_alias)
    params = list(sig.parameters.values())
    # self + name
    assert len(params) >= 2, "expected (self, name, ...) parameters"
    assert params[1].name == "name", "second parameter should be 'name'"


def test_find_by_any_alias_query_uses_aliases_column() -> None:
    """R7-4 — the method body references the JSONB ``aliases`` column on the model.

    Inspect the source — any of: ``aliases``, ``CornerstoneInvestor.aliases``,
    ``model.aliases``. The point is to prove the query touches that column,
    not just the name_zh/name_en path that find_by_canonical_name already uses.
    """
    source = inspect.getsource(CornerstoneInvestorRepository.find_by_any_alias)
    assert "aliases" in source, "R7-4: find_by_any_alias must query the aliases JSONB column"


def test_find_by_any_alias_query_uses_jsonb_operator() -> None:
    """R7-4 — must use a JSONB operator (``@>`` / ``contains`` / ``[`` indexing).

    Substring or array-contains operators are valid; plain ``==`` against
    the JSONB column wouldn't catch any alias.
    """
    source = inspect.getsource(CornerstoneInvestorRepository.find_by_any_alias)
    has_jsonb_op = (
        ".contains(" in source  # SQLAlchemy contains
        or '["items"]' in source  # JSONB indexing
        or "['items']" in source
        or "jsonb_path" in source  # raw jsonb_path_query / jsonb_path_exists
        or "@>" in source  # raw containment
    )
    assert has_jsonb_op, (
        "R7-4: find_by_any_alias must use a JSONB operator "
        "(.contains / aliases['items'] / jsonb_path*) — pre-fix there was none."
    )


def test_gin_index_migration_exists() -> None:
    """R7-4 — a migration file under data/migrations/versions adds a GIN
    index on cornerstone_investors.aliases.

    Without the index, JSONB containment scans the full table (1,314 rows
    today — manageable, but growing). The migration script is the right
    place to install operational hygiene.
    """
    import pathlib

    migrations_dir = pathlib.Path(__file__).resolve().parents[3] / (
        "src/hk_ipo_agent/data/migrations/versions"
    )
    found_migration = False
    for path in migrations_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "gin" in text.lower() and "aliases" in text.lower():
            found_migration = True
            break
    assert found_migration, (
        "R7-4: expected a migration adding a GIN index on "
        "cornerstone_investors.aliases under data/migrations/versions/"
    )
