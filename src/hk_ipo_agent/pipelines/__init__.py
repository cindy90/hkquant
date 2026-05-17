"""End-to-end pipelines that compose lower-level building blocks.

Each module here is a thin orchestration layer — it does **not** add new
domain logic, only wires existing pieces together for a specific entry
point (CLI / API / scheduled task).

See ADR 0016 §Decision third class for the rationale: a single canonical
"PDF → snapshot" pipeline replaces ad-hoc per-IPO scripts.
"""

from .pdf_to_snapshot import PipelineConfig, PipelineResult, run_pdf_to_snapshot

__all__ = ("PipelineConfig", "PipelineResult", "run_pdf_to_snapshot")
