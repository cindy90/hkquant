"""Immutable ``PredictionSnapshot`` builder + SHA-256 integrity helpers.

Per PROJECT_SPEC.md §3.11 / §6 + CLAUDE.md "prediction lifecycle"
constraints. ADR 0010 §3 confirms Phase 6 ships an in-memory snapshot;
Phase 7.5 will replace storage with PostgreSQL + DB trigger.

The ``PredictionSnapshot`` model itself is a ``FrozenModel`` defined in
``common/schemas.py`` (Pydantic-level immutability). DB-level UPDATE
prevention is wired in Phase 7.5.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel

from ..common.enums import ProspectusVersion
from ..common.schemas import (
    AgentOutput,
    DebateOutput,
    FinalDecision,
    PredictionSnapshot,
    ProspectusExtraction,
    ValuationEnsembleOutput,
)
from ..common.settings import get_settings


class SnapshotIntegrityError(Exception):
    """Raised when a snapshot's stored hash doesn't match its recomputed hash."""


def _stable_json(payload: Any) -> str:
    """Deterministic JSON dump for hash inputs.

    - Sorts keys recursively
    - Pydantic models are dumped via ``model_dump(mode='json')``
    - Decimals → strings (Pydantic default for mode='json')
    - UUIDs / datetimes → ISO strings (Pydantic default)
    """
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def compute_input_hash(
    *,
    extraction: ProspectusExtraction,
    agent_outputs: dict[str, AgentOutput],
    valuation: ValuationEnsembleOutput,
    debate: DebateOutput,
    decision: FinalDecision,
) -> str:
    """Return SHA-256 hex digest covering all 5 input artifacts.

    Used both as ``PredictionSnapshot.input_data_hash`` and for re-read
    integrity verification.
    """
    sorted_agents = {
        role: agent_outputs[role].model_dump(mode="json") for role in sorted(agent_outputs)
    }
    payload = {
        "extraction": extraction.model_dump(mode="json"),
        "agent_outputs": sorted_agents,
        "valuation": valuation.model_dump(mode="json"),
        "debate": debate.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_snapshot(
    *,
    ipo_id: UUID,
    extraction: ProspectusExtraction,
    agent_outputs: dict[str, AgentOutput],
    valuation: ValuationEnsembleOutput,
    debate: DebateOutput,
    decision: FinalDecision,
    total_cost_usd: Decimal,
    runtime_seconds: float,
    model_versions: dict[str, str] | None = None,
    config_snapshot: dict[str, Any] | None = None,
) -> PredictionSnapshot:
    """Build an immutable snapshot ready to persist.

    The ``id`` is a fresh UUID4; the integrity hash is computed deterministically.
    """
    settings = get_settings().orchestrator
    snap_id = uuid4()
    input_hash = compute_input_hash(
        extraction=extraction,
        agent_outputs=agent_outputs,
        valuation=valuation,
        debate=debate,
        decision=decision,
    )

    return PredictionSnapshot(
        id=snap_id,
        ipo_id=ipo_id,
        as_of_date=extraction.extracted_at.date(),
        prospectus_version=ProspectusVersion.PHIP.value,
        input_data_hash=input_hash,
        input_data_snapshot={
            "extraction": extraction.model_dump(mode="json"),
        },
        agent_outputs=agent_outputs,
        valuation_output=valuation,
        debate_output=debate,
        decision=decision,
        system_version=settings.system_version,
        model_versions=model_versions or {"synthesizer": "moonshot-v1-128k"},
        config_snapshot=config_snapshot or {},
        total_cost_usd=total_cost_usd,
        runtime_seconds=runtime_seconds,
        created_at=datetime.now(UTC),
    )


def verify_snapshot(snapshot: PredictionSnapshot) -> None:
    """Recompute hash; raise ``SnapshotIntegrityError`` if mismatch."""
    recomputed = compute_input_hash(
        extraction=ProspectusExtraction.model_validate(snapshot.input_data_snapshot["extraction"]),
        agent_outputs=snapshot.agent_outputs,
        valuation=snapshot.valuation_output,
        debate=snapshot.debate_output,
        decision=snapshot.decision,
    )
    if recomputed != snapshot.input_data_hash:
        raise SnapshotIntegrityError(
            f"Snapshot {snapshot.id}: stored hash {snapshot.input_data_hash} "
            f"!= recomputed {recomputed}"
        )


__all__ = (
    "SnapshotIntegrityError",
    "build_snapshot",
    "compute_input_hash",
    "verify_snapshot",
)
