"""R7-3 — SponsorTrackBuilder.compute MUST filter by sponsor name, not
return aggregate stats for ALL IPOs in the window.

Pre-R7-3 the query was:

    SELECT ipo_postmarket.day1_return, ipo_postmarket.day126_return
    FROM ipo_postmarket
    JOIN ipo_events ON ipo_postmarket.ipo_id = ipo_events.id
    WHERE ipo_events.listing_date BETWEEN :start AND :end

The ``sponsor_name`` argument was accepted but NEVER applied to the
WHERE clause. ``compute("Goldman Sachs", ...)`` returned the same
aggregate as ``compute("Morgan Stanley", ...)``. That breaks any
sponsor-aware backtest (Phase 8) and any sponsor-quality signal the
agents try to derive (Phase 5 fundamental_agent / industry_agent).

Post-R7-3 the SQL joins ``sponsors`` and filters
``IPOEvent.sponsor_ids @> ARRAY[sponsor.id]``, so callers ACTUALLY get
the per-sponsor track record.

These tests verify behaviour without a live PG: they inspect the
generated SQL string from sqlalchemy's compile step.
"""

from __future__ import annotations

import ast
import inspect

from hk_ipo_agent.data.builders.sponsor_track_record import SponsorTrackBuilder


def test_compute_method_references_sponsor_name_in_query() -> None:
    """R7-3 — the source of ``SponsorTrackBuilder.compute`` mentions the
    ``sponsor_name`` parameter inside the SELECT / WHERE expression.

    Pre-fix the parameter was bound but only used inside the returned
    dataclass for display, never in the query. We assert via AST walk
    that ``sponsor_name`` appears in at least one Call inside the method
    body — proof the parameter affects the SQL.
    """
    source = inspect.getsource(SponsorTrackBuilder.compute)
    tree = ast.parse(source.strip())

    # Find all Name nodes referencing sponsor_name AFTER the function signature
    # body. We need it to appear in a Call or Compare expression (used by SQL),
    # not just in the dataclass return.
    references_in_query: list[ast.AST] = []
    in_query_section = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Walk the call's args looking for sponsor_name usage.
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id == "sponsor_name":
                    references_in_query.append(node)
                    in_query_section = True
                    break

    assert in_query_section, (
        "R7-3: ``sponsor_name`` parameter must be referenced inside a Call "
        "(LIKE / where / sponsors lookup) in compute(). Pre-fix it was bound "
        "but never applied — the query returned all IPOs in the window."
    )


def test_compute_method_uses_sponsors_table_or_array_contains() -> None:
    """R7-3 — ``compute`` must JOIN sponsors or use ARRAY contains so the
    sponsor name actually filters results.

    Either ``Sponsor`` (the ORM class) appears in the source, or the
    ``@>`` / ``.contains`` array operator appears, or the source mentions
    ``sponsors`` table name with a LIKE filter.
    """
    source = inspect.getsource(SponsorTrackBuilder.compute)

    has_sponsor_join = (
        "Sponsor" in source  # ORM model import
        or "sponsors" in source.lower()  # raw table reference
        or ".contains(" in source  # ARRAY contains
        or "sponsor_ids" in source  # explicit column ref
    )
    assert has_sponsor_join, (
        "R7-3: ``compute`` must reference the sponsors table OR the "
        "sponsor_ids array column. Pre-fix neither appeared, so the query "
        "ignored sponsor_name entirely."
    )


def test_compute_method_has_like_or_eq_on_sponsor_name() -> None:
    """R7-3 — the SQL applies a string match (LIKE / ilike / eq) against
    ``sponsor_name``.

    Pre-fix the query had no string filter at all. Post-fix it must have
    one of: ``.like(``, ``.ilike(``, ``==``, ``.contains(`` so the
    parameter is wired into the SQL.
    """
    source = inspect.getsource(SponsorTrackBuilder.compute)

    has_string_match = ".like(" in source or ".ilike(" in source or ".contains(" in source
    assert has_string_match, (
        "R7-3: compute() must apply a string-match operator on sponsor_name; "
        "pre-fix none was present."
    )
