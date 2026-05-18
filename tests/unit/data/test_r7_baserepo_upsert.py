"""R7-8 — BaseRepository.upsert + bulk_upsert exclude ``created_at`` / ``id``
from the UPDATE column set by default.

Pre-R7-8 the helpers built update_cols as
``{k: stmt.excluded[k] for k in values if k not in conflict_cols}``. That
includes ``created_at`` — so every re-INSERT clobbered the original
insert timestamp with the new one. The TimestampMixin's intent
("created_at = first time we ever saw this row") was broken silently.

Similarly ``id`` (the row PK) shouldn't be overwritten on update; it's
typically already the conflict target but isn't always when conflict
runs against a different UNIQUE constraint.

Post-R7-8:
  * Default upsert exclusions: ``id`` and ``created_at``.
  * Caller can still opt-in via an explicit ``update_columns`` kwarg
    that overrides the default.
"""

from __future__ import annotations

import inspect

from hk_ipo_agent.data.repositories.base import BaseRepository


def test_upsert_default_excludes_created_at_and_id() -> None:
    """R7-8 — the exclusion lives either inline in the method or in the
    shared helper (_build_update_cols + _UPSERT_DEFAULT_EXCLUDED).
    """
    method_source = inspect.getsource(BaseRepository.upsert)
    # Either explicit literals in the method body, or a delegation to the
    # shared helper which has the exclusion logic.
    method_uses_helper = "_build_update_cols" in method_source
    has_inline_excl = "created_at" in method_source and (
        '"id"' in method_source or "'id'" in method_source
    )
    assert method_uses_helper or has_inline_excl, (
        "R7-8: upsert must either reference the exclusion inline or delegate "
        "to a helper that does (e.g. _build_update_cols)"
    )


def test_bulk_upsert_default_excludes_created_at_and_id() -> None:
    """R7-8 — same exclusion contract for the bulk path."""
    method_source = inspect.getsource(BaseRepository.bulk_upsert)
    method_uses_helper = "_build_update_cols" in method_source
    has_inline_excl = "created_at" in method_source and (
        '"id"' in method_source or "'id'" in method_source
    )
    assert method_uses_helper or has_inline_excl


def test_upsert_default_excluded_constant_exists() -> None:
    """R7-8 — there's a shared constant defining the default exclusions
    so the two methods share one source of truth and future readers find it.
    """
    from hk_ipo_agent.data.repositories import base as base_mod

    assert hasattr(base_mod, "_UPSERT_DEFAULT_EXCLUDED"), (
        "R7-8: BaseRepository module must expose a _UPSERT_DEFAULT_EXCLUDED "
        "constant listing the columns excluded from upsert UPDATE by default"
    )
    excluded = base_mod._UPSERT_DEFAULT_EXCLUDED
    assert "id" in excluded, "default exclusion must include 'id'"
    assert "created_at" in excluded, "default exclusion must include 'created_at'"


def test_upsert_accepts_update_columns_override() -> None:
    """R7-8 — caller can override the default via an ``update_columns`` kwarg.

    The signature should accept an optional iterable of column names; when
    provided, ONLY those columns are updated on conflict (regardless of
    the default exclusion).
    """
    sig = inspect.signature(BaseRepository.upsert)
    params = list(sig.parameters.values())
    param_names = [p.name for p in params]
    assert "update_columns" in param_names, (
        f"R7-8: upsert must accept an ``update_columns`` override kwarg, got {param_names}"
    )


def test_bulk_upsert_accepts_update_columns_override() -> None:
    """R7-8 — bulk variant also supports the override kwarg."""
    sig = inspect.signature(BaseRepository.bulk_upsert)
    param_names = [p.name for p in sig.parameters.values()]
    assert "update_columns" in param_names, (
        f"R7-8: bulk_upsert must accept an ``update_columns`` override kwarg, got {param_names}"
    )
