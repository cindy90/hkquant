"""R3-8 — review_proposals.py ``apply`` subcommand.

Pre-R3-8 the human-gated apply step lived only as hand-rolled
``python -c "import asyncio; ..."`` in LEARNING_PROTOCOL.md, which was
fragile and error-prone. R3-8 adds an ``apply <review_id>`` subcommand
to the same CLI so the protocol fits in one tool.

These tests pin the CLI's argparse surface (no PG required).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

# scripts/review_proposals.py isn't a package — import via path injection.
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import review_proposals as rp


def test_apply_subcommand_accepts_proposed_content_arg() -> None:
    """R3-8 — apply subcommand accepts --proposed-content + --applied-by."""
    review_id = uuid.uuid4()
    args = rp._parse_args(
        [
            "apply",
            str(review_id),
            "--applied-by",
            "alice@hk.local",
            "--proposed-content",
            "path/to/new_weights.json",
            "--proposal-index",
            "2",
        ]
    )
    assert args.cmd == "apply"
    assert args.review_id == review_id
    assert args.applied_by == "alice@hk.local"
    assert args.proposed_content == Path("path/to/new_weights.json")
    assert args.proposal_index == 2


def test_apply_subcommand_proposed_content_is_optional() -> None:
    """R3-8 — --proposed-content defaults to None (caller can rely on
    proposal.proposed_value if it's not None; otherwise applier rejects
    per R3-7)."""
    review_id = uuid.uuid4()
    args = rp._parse_args(
        [
            "apply",
            str(review_id),
            "--applied-by",
            "alice@hk.local",
        ]
    )
    assert args.proposed_content is None
    assert args.proposal_index == 0


def test_apply_subcommand_requires_applied_by() -> None:
    """R3-8 — applied_by is required (CLI mirrors SOX-style audit subject).
    argparse exits 2 on missing required arg."""
    review_id = uuid.uuid4()
    with pytest.raises(SystemExit) as exc_info:
        rp._parse_args(["apply", str(review_id)])
    assert exc_info.value.code != 0


def test_existing_subcommands_unchanged() -> None:
    """Regression: R3-8 must not break the existing list/accept/reject CLI."""
    review_id = uuid.uuid4()

    args = rp._parse_args(["list"])
    assert args.cmd == "list"

    args = rp._parse_args(["accept", str(review_id), "--reviewer", "alice"])
    assert args.cmd == "accept"
    assert args.reviewer == "alice"

    args = rp._parse_args(
        ["reject", str(review_id), "--reviewer", "bob", "--reason", "regression risk"]
    )
    assert args.cmd == "reject"
    assert args.reason == "regression risk"
