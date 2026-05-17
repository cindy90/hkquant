"""End-to-end PDF → snapshot pipeline (ADR 0016 §Decision third class).

Replaces the one-off ``scripts/run_e2e_test.py``: the 5 steps
(parse → chunk → extract → graph → report) are now a single function
parametrised by ``PipelineConfig``. ``scripts/analyze_pdf.py`` is the
CLI front-door; ``tests/e2e/test_yifei_case.py`` is the regression case
that exercises the same function with the 翼菲智能 PDF.

Why this exists: every time we analysed a new IPO before, we copied
``run_e2e_test.py`` and hand-edited the hard-coded ticker / company name
/ listing type, which is exactly how ``evaluate_new_ipo.py`` and
``search_yifei_tech.py`` (both archived to legacy/ in 0be44f4) were
born. ADR 0016 closes that recurrence at its source.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dc_fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..agents.workflow_extras import WorkflowExtras
from ..common.enums import ListingType
from ..common.llm_client import LLMClient
from ..common.settings import clear_config_caches
from ..orchestrator.graph import build_main_graph
from ..prediction_registry.registry import InMemoryPredictionRegistry, set_registry
from ..prospectus.chunker import ChunkConfig, chunk_document
from ..prospectus.extractor import ExtractionConfig, ProspectusExtractor
from ..prospectus.parser import ParserConfig, parse_prospectus
from ..valuation.base import MarketData


class PipelineConfig(BaseModel):
    """Inputs to ``run_pdf_to_snapshot``.

    Everything that ``run_e2e_test.py`` hard-coded is now an explicit
    field. Defaults reflect a 18C tech IPO with no special configuration;
    callers (CLI / tests) override what they need.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    pdf_path: Path
    ipo_id: str = Field(..., description="HK ticker, e.g. '6871.HK'")
    prospectus_id: str = Field(..., description="Stable ID for the prospectus document")
    company_name_zh: str
    listing_type: ListingType = ListingType.CH18C_COMMERCIALIZED
    industry_code: str = "unknown"
    industry_description: str = ""

    # Parser / chunker / extractor tunables.
    max_pages: int = 500
    chunk_target_chars: int = 1500
    chunk_max_chars: int = 2500
    max_chunks_per_section: int = 10
    prefer_llamaparse: bool = False

    # Reporting.
    write_report: bool = True
    output_dir: Path | None = None  # defaults to <project_root>/outputs


@dataclass
class PipelineResult:
    """Return value of ``run_pdf_to_snapshot``.

    Carries enough state for both CLI pretty-printing and pytest
    assertions without forcing either consumer to re-derive things.
    """

    parsed_doc: Any
    chunks: list[Any]
    extraction_result: Any
    final_state: dict[str, Any]
    snapshot_id: str | None
    total_cost_usd: Decimal
    total_elapsed_s: float
    report_path: Path | None = None
    step_timings_s: dict[str, float] = field(default_factory=dict)


# Heuristic section classifier — preserved verbatim from run_e2e_test.py
# because it's load-bearing for real (non-fixture) PDFs whose TOC entries
# trip the structural section detector.
_FIN_KEYWORDS_CJK = (
    "收入", "收益", "營業額", "毛利", "淨利潤", "净利润",
    "資產負債", "资产负债", "現金流量", "现金流量",
    "經營業績", "财务", "財務",
)
_RISK_KEYWORDS_CJK = ("風險", "风险", "不確定", "不确定")
_BIZ_KEYWORDS_CJK = (
    "業務", "业务", "產品", "产品", "服務", "服务",
    "客戶", "客户", "市場", "市场", "研發", "研发", "技術", "技术",
)
_SHAREHOLDER_KEYWORDS_CJK = (
    "股東", "股东", "基石投資", "基石投资", "股本", "持股", "配售",
)
_FIN_RE_EN = re.compile(r"(revenue|profit|loss|ebitda|cash flow)", re.IGNORECASE)
_RISK_RE_EN = re.compile(r"(risk factor|uncertaint)", re.IGNORECASE)


def _classify_chunk(text: str) -> str:
    if any(kw in text for kw in _FIN_KEYWORDS_CJK) or _FIN_RE_EN.search(text):
        return "financials"
    if any(kw in text for kw in _RISK_KEYWORDS_CJK) or _RISK_RE_EN.search(text):
        return "risks"
    if any(kw in text for kw in _BIZ_KEYWORDS_CJK):
        return "business"
    if any(kw in text for kw in _SHAREHOLDER_KEYWORDS_CJK):
        return "shareholders"
    return "other"


def _group_chunks_by_section(chunks: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """Always content-classify; section headers in HK prospectuses are
    unreliable (TOC + summary echoes confuse the structural detector).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        section = _classify_chunk(chunk.text)
        if section == "other":
            continue
        groups.setdefault(section, []).append({
            "text": chunk.text,
            "page": chunk.page,
            "chunk_id": chunk.chunk_id,
        })
    return groups


async def run_pdf_to_snapshot(
    config: PipelineConfig,
    market_data: MarketData,
    *,
    llm_client: LLMClient,
    use_checkpointer: bool = False,
    log: Callable[[str], None] = print,
) -> PipelineResult:
    """Run the full PDF → snapshot pipeline.

    Args:
        config: All IPO-specific inputs.
        market_data: Peer multiples / regime / risk-free / ERP. Built by
            the caller because it's the one piece that legitimately
            varies per IPO without belonging in PipelineConfig (which is
            about *what to analyse*, not *what the market looks like*).
        llm_client: Pre-built LLMClient. Injected so tests can swap in a
            mock and so the CLI controls budget.
        use_checkpointer: Pass-through to ``build_main_graph``.
        log: Where to emit step banners. Defaults to ``print``; tests
            pass a noop.

    Returns:
        PipelineResult — all intermediates + the final snapshot id.

    Raises:
        FileNotFoundError: if config.pdf_path doesn't exist.
        Anything raised by underlying parse / chunk / extract / graph
            invocation — not silently swallowed.
    """
    if not config.pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {config.pdf_path}")

    clear_config_caches()
    timings: dict[str, float] = {}
    t_total = time.time()

    # --- Step 1: parse ----------------------------------------------------
    log(f"[1/5] Parsing PDF ({config.pdf_path.name}) ...")
    t0 = time.time()
    parsed_doc = await parse_prospectus(
        config.pdf_path,
        prospectus_id=config.prospectus_id,
        config=ParserConfig(
            prefer_llamaparse=config.prefer_llamaparse,
            max_pages=config.max_pages,
        ),
    )
    timings["parse"] = time.time() - t0
    log(
        f"      backend={parsed_doc.backend} pages={parsed_doc.page_count} "
        f"blocks={len(parsed_doc.blocks)} chars={len(parsed_doc.full_text):,}"
    )

    # --- Step 2: chunk ----------------------------------------------------
    log("[2/5] Chunking ...")
    t0 = time.time()
    chunks = chunk_document(
        parsed_doc,
        config=ChunkConfig(
            target_chars=config.chunk_target_chars,
            max_chars=config.chunk_max_chars,
        ),
    )
    timings["chunk"] = time.time() - t0
    section_counts: Counter[str | None] = Counter(c.section for c in chunks)
    log(f"      total={len(chunks)} sections={dict(section_counts)}")

    # --- Step 3: extract --------------------------------------------------
    log("[3/5] LLM extraction ...")
    t0 = time.time()
    chunks_by_section = _group_chunks_by_section(chunks)
    extractor = ProspectusExtractor(
        llm_client,
        config.prospectus_id,
        config=ExtractionConfig(
            company_name_zh=config.company_name_zh,
            listing_type=config.listing_type,
            industry_code=config.industry_code,
            industry_description=config.industry_description,
            max_chunks_per_section=config.max_chunks_per_section,
        ),
    )
    extraction_result = await extractor.extract(chunks_by_section)
    timings["extract"] = time.time() - t0
    log(
        f"      routed={extraction_result.sections_routed} "
        f"ok={extraction_result.sections_succeeded} "
        f"fail={extraction_result.sections_failed} "
        f"cost=${extraction_result.total_cost_usd:.4f}"
    )

    # --- Step 4: graph ----------------------------------------------------
    log("[4/5] LangGraph (7 agents + debate + synthesizer + snapshot) ...")
    t0 = time.time()
    set_registry(InMemoryPredictionRegistry())
    initial_state = {
        "ipo_id": config.ipo_id,
        "prospectus_id": config.prospectus_id,
        "as_of_date": market_data.as_of_date,
        "extraction": extraction_result.extraction,
        "extras": WorkflowExtras(),
        "agent_outputs": {},
        "runtime_meta": {"started_at": time.time()},
    }
    graph = build_main_graph(
        llm_client=llm_client,
        market_data=market_data,
        use_checkpointer=use_checkpointer,
    )
    final_state = await graph.ainvoke(initial_state)
    timings["graph"] = time.time() - t0
    decision = final_state.get("decision")
    log(
        f"      decision={getattr(decision, 'decision', None)} "
        f"snapshot={final_state.get('snapshot_id')}"
    )

    # --- Step 5: report ---------------------------------------------------
    report_path: Path | None = None
    if config.write_report:
        log("[5/5] Writing reports (summary + detailed + JSON) ...")
        t0 = time.time()
        report_path = _write_report(
            config=config,
            final_state=final_state,
            llm_client=llm_client,
            total_elapsed_s=time.time() - t_total,
        )
        detailed_path = _write_detailed_report(
            config=config,
            final_state=final_state,
            llm_client=llm_client,
            total_elapsed_s=time.time() - t_total,
        )
        json_path = _dump_full_state_json(
            config=config,
            final_state=final_state,
            llm_client=llm_client,
            total_elapsed_s=time.time() - t_total,
        )
        timings["report"] = time.time() - t0
        log(f"      summary:  {report_path}")
        log(f"      detailed: {detailed_path}")
        log(f"      json:     {json_path}")

    total_elapsed = time.time() - t_total
    return PipelineResult(
        parsed_doc=parsed_doc,
        chunks=chunks,
        extraction_result=extraction_result,
        final_state=final_state,
        snapshot_id=final_state.get("snapshot_id"),
        total_cost_usd=llm_client.cost_log.total_usd(),
        total_elapsed_s=total_elapsed,
        report_path=report_path,
        step_timings_s=timings,
    )


def _write_report(
    *,
    config: PipelineConfig,
    final_state: dict[str, Any],
    llm_client: LLMClient,
    total_elapsed_s: float,
) -> Path:
    """Write the per-IPO markdown report. Returns the written path."""
    out_dir = config.output_dir or Path(__file__).resolve().parents[3] / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    path = out_dir / f"{config.ipo_id}_analysis_{today}.md"

    decision = final_state.get("decision")
    agent_outputs = final_state.get("agent_outputs", {})
    cost_log = llm_client.cost_log

    lines: list[str] = [
        f"# {config.ipo_id} {config.company_name_zh} — Cornerstone Decision Memo",
        "",
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"> Listing type: {config.listing_type.value}  ",
        f"> Snapshot: `{final_state.get('snapshot_id')}`  ",
        "> Pipeline: ``pipelines.pdf_to_snapshot`` (ADR 0016)",
        "",
        "---",
        "",
        "## Decision",
        "",
    ]
    if decision is not None:
        lines += [
            f"- **Decision**: {decision.decision.value.upper()}",
            f"- **Confidence**: {decision.confidence:.1%}",
            f"- **Price range** (low / fair / high): "
            f"{decision.price_range_low} / {decision.price_range_fair} / {decision.price_range_high}",
            "",
            "### Scorecard",
            "",
            "| Dimension | Score |",
            "|---|---|",
        ]
        for k, v in decision.scorecard.items():
            lines.append(f"| {k} | {v:.1f} |")
        lines += [
            "",
            "### Reasons for",
            *(f"- {r}" for r in decision.key_reasons_for),
            "",
            "### Reasons against",
            *(f"- {r}" for r in decision.key_reasons_against),
            "",
        ]
    else:
        lines.append("_No FinalDecision was produced — see graph trace._")
        lines.append("")

    lines += [
        "## 7-Agent scorecard",
        "",
        "| Agent | Score | #Findings |",
        "|---|---|---|",
    ]
    for role, output in sorted(agent_outputs.items()):
        findings_count = len(output.key_findings) if output.key_findings else 0
        lines.append(f"| {role} | {output.overall_score:.1f} | {findings_count} |")

    lines += [
        "",
        "## Cost & performance",
        "",
        f"- Total LLM cost: ${cost_log.total_usd():.4f}",
        f"- Total runtime: {total_elapsed_s:.1f}s ({total_elapsed_s / 60:.1f} min)",
        f"- LLM calls: {len(cost_log.records)}",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _dump_full_state_json(
    *,
    config: PipelineConfig,
    final_state: dict[str, Any],
    llm_client: LLMClient,
    total_elapsed_s: float,
) -> Path:
    """Dump the complete pipeline state to JSON for programmatic access."""
    out_dir = config.output_dir or Path(__file__).resolve().parents[3] / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{config.ipo_id}_full_state_{ts}.json"

    decision = final_state.get("decision")
    agent_outputs = final_state.get("agent_outputs", {})
    debate = final_state.get("debate_output")
    valuation = final_state.get("valuation_output")
    extras = final_state.get("extras")
    cost_log = llm_client.cost_log

    def _default(obj: object) -> object:
        if isinstance(obj, Decimal):
            return str(obj)
        if hasattr(obj, "isoformat"):
            return obj.isoformat()  # type: ignore[union-attr]
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")  # type: ignore[union-attr]
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)  # type: ignore[arg-type]
        raise TypeError(f"Not serializable: {type(obj).__name__}")

    state_dict: dict[str, Any] = {
        "meta": {
            "ipo_id": config.ipo_id,
            "prospectus_id": config.prospectus_id,
            "company_name_zh": config.company_name_zh,
            "listing_type": config.listing_type.value,
            "generated_at": datetime.now().isoformat(),
            "total_elapsed_seconds": round(total_elapsed_s, 2),
            "total_cost_usd": str(cost_log.total_usd()),
            "llm_calls": len(cost_log.records),
            "snapshot_id": str(final_state.get("snapshot_id")) if final_state.get("snapshot_id") else None,
        },
        "extraction": (
            final_state["extraction"].model_dump(mode="json")
            if final_state.get("extraction") and hasattr(final_state["extraction"], "model_dump")
            else None
        ),
        "agent_outputs": {
            role: output.model_dump(mode="json")
            for role, output in agent_outputs.items()
        },
        "valuation_output": (
            valuation.model_dump(mode="json")
            if valuation and hasattr(valuation, "model_dump")
            else None
        ),
        "debate_output": (
            debate.model_dump(mode="json")
            if debate and hasattr(debate, "model_dump")
            else None
        ),
        "cross_check_notes": final_state.get("cross_check_notes", []),
        "extras": None,
        "decision": (
            decision.model_dump(mode="json")
            if decision and hasattr(decision, "model_dump")
            else None
        ),
        "cost_records": [
            {
                "model": r.model,
                "agent_role": r.agent_role,
                "ipo_id": r.ipo_id,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "cost_usd": str(r.cost_usd),
                "runtime_seconds": round(r.runtime_seconds, 3),
                "request_id": r.request_id,
            }
            for r in cost_log.records
        ],
    }

    if extras:
        try:
            state_dict["extras"] = asdict(extras)
        except Exception:
            state_dict["extras"] = str(extras)

    path.write_text(
        json.dumps(state_dict, ensure_ascii=False, indent=2, default=_default),
        encoding="utf-8",
    )
    return path


def _write_detailed_report(  # noqa: PLR0912, PLR0915
    *,
    config: PipelineConfig,
    final_state: dict[str, Any],
    llm_client: LLMClient,
    total_elapsed_s: float,
) -> Path:
    """Write a comprehensive markdown report with all intermediate analysis results.

    noqa rationale: linear markdown report builder — branches and statements
    map 1:1 to report sections (decision / scorecard / per-agent findings /
    debate rounds / valuation breakdown / extras). Splitting would obscure
    rather than clarify section ordering.
    """
    out_dir = config.output_dir or Path(__file__).resolve().parents[3] / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{config.ipo_id}_detailed_analysis_{ts}.md"

    decision = final_state.get("decision")
    ext = final_state.get("extraction")
    agent_outputs = final_state.get("agent_outputs", {})
    debate = final_state.get("debate_output")
    valuation = final_state.get("valuation_output")
    extras = final_state.get("extras")
    cross_check = final_state.get("cross_check_notes", [])
    snapshot_id = final_state.get("snapshot_id")
    cost_log = llm_client.cost_log

    L: list[str] = []

    # ===== Header =====
    L.append(f"# {config.company_name_zh} ({config.ipo_id}) 多Agent深度分析报告")
    L.append("")
    L.append(f"> **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append("> **Pipeline**: PDF解析 → 分块 → LLM抽取 → 7 Agent并行 → 估值 → 辩论 → 综合决策")
    L.append("> **LLM**: KIMI moonshot-v1-128k (via Moonshot API)")
    L.append(f"> **上市规则**: {config.listing_type.value}")
    L.append(f"> **Snapshot ID**: `{snapshot_id}`")
    L.append("")
    L.append("---")
    L.append("")

    # ===== 1. Executive Summary =====
    L.append("## 1. 执行摘要")
    L.append("")
    if decision:
        L.append("| 项目 | 结果 |")
        L.append("|------|------|")
        L.append(f"| **最终决策** | **{decision.decision.value.upper()}** |")
        L.append(f"| 置信度 | {decision.confidence:.1%} |")
        L.append(f"| 综合评分 | {decision.scorecard.get('overall', 0):.1f} / 100 |")
        if decision.suggested_allocation_pct is not None:
            L.append(f"| 建议仓位 | {decision.suggested_allocation_pct:.2%} |")
        L.append(f"| 价格区间 (RMB) | {decision.price_range_low:,.0f} / {decision.price_range_fair:,.0f} / {decision.price_range_high:,.0f} |")
        L.append(f"| 分析成本 | ${cost_log.total_usd():.4f} |")
        L.append(f"| 分析耗时 | {total_elapsed_s:.1f}s |")
    L.append("")

    # ===== 2. Extraction =====
    L.append("## 2. 招股书结构化抽取")
    L.append("")
    if ext:
        L.append("### 2.1 基本信息")
        L.append("")
        L.append("| 字段 | 值 |")
        L.append("|------|------|")
        L.append(f"| 公司名称(中) | {ext.company_name_zh} |")
        L.append(f"| 公司名称(英) | {ext.company_name_en or '未提取'} |")
        L.append(f"| 股票代码 | {ext.stock_code or '未提取'} |")
        L.append(f"| 上市规则 | {ext.listing_type.value} |")
        L.append(f"| 行业 | {ext.industry_code} — {ext.industry_description} |")
        L.append(f"| 需人工复核 | {'是' if ext.needs_human_review else '否'} |")
        L.append("")

        L.append("### 2.2 业务模式")
        L.append("")
        L.append(ext.business_model if ext.business_model else "(未提取)")
        L.append("")

        if ext.revenue_streams:
            L.append("### 2.3 收入来源")
            L.append("")
            for i, rs in enumerate(ext.revenue_streams, 1):
                if isinstance(rs, dict):
                    L.append(f"**{i}. {rs.get('name', '未命名')}**")
                    for k, v in rs.items():
                        if k != "name":
                            L.append(f"  - {k}: {v}")
                else:
                    L.append(f"- {rs}")
            L.append("")

        if ext.financials:
            L.append("### 2.4 财务数据")
            L.append("")
            L.append("| 年度 | 期间 | 收入(RMB) | 毛利(RMB) | 毛利率 | 净利润(RMB) | 研发费用(RMB) | 研发占比 | 经营现金流(RMB) |")
            L.append("|------|------|-----------|-----------|--------|-------------|-------------|---------|----------------|")
            for fs in ext.financials:
                rev = f"{fs.revenue_rmb:,.0f}" if fs.revenue_rmb else "-"
                gp = f"{fs.gross_profit_rmb:,.0f}" if fs.gross_profit_rmb else "-"
                gm = f"{fs.gross_margin:.1%}" if fs.gross_margin is not None else "-"
                np_ = f"{fs.net_profit_rmb:,.0f}" if fs.net_profit_rmb else "-"
                rd = f"{fs.rd_expense_rmb:,.0f}" if fs.rd_expense_rmb else "-"
                rd_pct = f"{fs.rd_pct_of_revenue:.1%}" if fs.rd_pct_of_revenue is not None else "-"
                ocf = f"{fs.operating_cash_flow_rmb:,.0f}" if fs.operating_cash_flow_rmb else "-"
                L.append(f"| {fs.fiscal_year} | {fs.fiscal_period} | {rev} | {gp} | {gm} | {np_} | {rd} | {rd_pct} | {ocf} |")
            L.append("")

        if ext.risk_factors:
            L.append(f"### 2.5 风险因素 ({len(ext.risk_factors)} 项)")
            L.append("")
            L.append("| # | 类别 | 严重性 | 描述 |")
            L.append("|---|------|--------|------|")
            for i, rf in enumerate(ext.risk_factors, 1):
                L.append(f"| {i} | {rf.category} | {rf.severity} | {rf.description[:150]} |")
            L.append("")

        if ext.shareholders:
            L.append(f"### 2.6 股东结构 ({len(ext.shareholders)} 位)")
            L.append("")
            L.append("| 股东 | IPO前持股 | 控股 | Pre-IPO投资者 | 上轮估值(RMB) | 上轮日期 |")
            L.append("|------|----------|------|-------------|-------------|---------|")
            for sh in ext.shareholders:
                val = f"{sh.last_round_valuation_rmb:,.0f}" if sh.last_round_valuation_rmb else "-"
                dt = str(sh.last_round_date) if sh.last_round_date else "-"
                L.append(f"| {sh.name} | {sh.pct_pre_ipo:.2%} | {'是' if sh.is_controlling else '否'} | {'是' if sh.is_pre_ipo_investor else '否'} | {val} | {dt} |")
            L.append("")

        if ext.review_reasons:
            L.append("### 2.7 需人工复核原因")
            L.append("")
            for reason in ext.review_reasons:
                L.append(f"- {reason}")
            L.append("")

    # ===== 3. Agent Details =====
    L.append("## 3. ��Agent详细分析")
    L.append("")
    agent_list = sorted(agent_outputs.keys())
    for idx, role in enumerate(agent_list, 1):
        output = agent_outputs[role]
        L.append(f"### 3.{idx} {role.replace('_', ' ').title()} Agent")
        L.append("")
        L.append("| 指标 | 值 |")
        L.append("|------|------|")
        L.append(f"| 综合得分 | **{output.overall_score:.1f}** / 100 |")
        L.append(f"| 成本 | ${output.cost_usd} |")
        L.append(f"| 用时 | {output.runtime_seconds:.1f}s |")
        L.append("")

        if output.scores:
            L.append("**维度得分:**")
            L.append("")
            L.append("| 子维度 | 得分 |")
            L.append("|--------|------|")
            for dim, score in output.scores.items():
                L.append(f"| {dim} | {score:.1f} |")
            L.append("")

        if output.key_findings:
            L.append(f"**关键发现 ({len(output.key_findings)} 项):**")
            L.append("")
            for i, f in enumerate(output.key_findings, 1):
                L.append(f"{i}. **{f.statement}** (置信度: {f.confidence.value})")
                if f.evidence:
                    L.append(f"   - 证据: {f.evidence}")
                if f.citations:
                    cite_str = "; ".join(
                        f"p.{c.page}" + (f" [{c.chunk_id}]" if c.chunk_id else "")
                        for c in f.citations
                    )
                    L.append(f"   - 引用: {cite_str}")
            L.append("")

        if output.uncertainty_flags:
            L.append("**不确定性标记:**")
            L.append("")
            for flag in output.uncertainty_flags:
                L.append(f"- {flag}")
            L.append("")

        if output.data_sources_used:
            L.append("**数据来源:**")
            L.append("")
            for ds in output.data_sources_used:
                L.append(f"- [{ds.source}] {ds.detail}")
            L.append("")

        L.append("---")
        L.append("")

    # ===== 4. Valuation =====
    L.append("## 4. 估值集成 (Valuation Ensemble)")
    L.append("")
    if valuation:
        L.append("### 4.1 单模型估值")
        L.append("")
        L.append("| 模型 | 适用 | P10 | P25 | P50 | P75 | P90 | 均值 | 标准差 |")
        L.append("|------|------|-----|-----|-----|-----|-----|------|--------|")
        for sm in valuation.single_models:
            d = sm.valuation_distribution
            appl = "是" if sm.applicable else "否"
            L.append(
                f"| {sm.model_name} | {appl} | "
                f"{d.p10:,.0f} | {d.p25:,.0f} | {d.p50:,.0f} | {d.p75:,.0f} | {d.p90:,.0f} | "
                f"{d.mean:,.0f} | {d.std:,.0f} |"
            )
        L.append("")

        if valuation.single_models:
            L.append("**各模型关键假设:**")
            L.append("")
            for sm in valuation.single_models:
                if sm.key_assumptions:
                    L.append(f"- **{sm.model_name}**: {sm.key_assumptions}")
            L.append("")

        L.append("### 4.2 权重分配")
        L.append("")
        L.append("| 模型 | 权重 |")
        L.append("|------|------|")
        for model_name, weight in valuation.weights_used.items():
            L.append(f"| {model_name} | {weight:.2%} |")
        L.append("")

        L.append("### 4.3 集成分布")
        L.append("")
        ed = valuation.ensemble_distribution
        L.append("| 分位数 | 估值 (RMB) |")
        L.append("|--------|-----------|")
        L.append(f"| P10 | {ed.p10:,.0f} |")
        L.append(f"| P25 | {ed.p25:,.0f} |")
        L.append(f"| **P50 (中位公允值)** | **{ed.p50:,.0f}** |")
        L.append(f"| P75 | {ed.p75:,.0f} |")
        L.append(f"| P90 | {ed.p90:,.0f} |")
        L.append(f"| 均值 | {ed.mean:,.0f} |")
        L.append(f"| 标准差 | {ed.std:,.0f} |")
        L.append("")

        if valuation.implied_price_range:
            L.append("### 4.4 隐含价格区间")
            L.append("")
            for k, v in valuation.implied_price_range.items():
                L.append(f"- {k}: RMB {v:,.0f}")
            L.append("")

        if valuation.notes:
            L.append("### 4.5 估值备注")
            L.append("")
            for note in valuation.notes:
                L.append(f"- {note}")
            L.append("")
    else:
        L.append("(无估值数据)")
        L.append("")

    # ===== 5. Debate =====
    L.append("## 5. Bull-Bear-Devil 辩论过程")
    L.append("")
    if debate and debate.rounds:
        L.append(f"共 **{len(debate.rounds)}** 轮辩论")
        L.append("")
        for rnd in debate.rounds:
            L.append(f"### 第 {rnd.round_number} 轮")
            L.append("")
            L.append("**Bull (看多方):**")
            L.append(f"> {rnd.bull_argument}")
            L.append("")
            L.append("**Bear (看空方):**")
            L.append(f"> {rnd.bear_argument}")
            L.append("")
            L.append("**Devil's Advocate (质疑方):**")
            L.append(f"> {rnd.devil_challenge}")
            L.append("")
            if rnd.resolution:
                L.append(f"*本轮结论*: {rnd.resolution}")
                L.append("")
            L.append("---")
            L.append("")

        L.append("### 最终共识")
        L.append("")
        L.append(debate.final_consensus)
        L.append("")

        if debate.unresolved_issues:
            L.append("### 未解决问题")
            L.append("")
            for issue in debate.unresolved_issues:
                L.append(f"- {issue}")
            L.append("")
    else:
        L.append("(无辩论数据)")
        L.append("")

    # ===== 6. Cross-check =====
    L.append("## 6. 交叉验证")
    L.append("")
    if cross_check:
        for note in cross_check:
            L.append(f"- {note}")
    else:
        L.append("(无交叉验证备注)")
    L.append("")

    # ===== 7. WorkflowExtras =====
    L.append("## 7. NACS 信号与调节因子 (WorkflowExtras)")
    L.append("")
    if extras:
        L.append("| 信号 | 值 |")
        L.append("|------|------|")
        try:
            for f in dc_fields(extras):
                val = getattr(extras, f.name, None)
                if f.name != "misc":
                    L.append(f"| {f.name} | {val} |")
            if hasattr(extras, "misc") and extras.misc:
                for k, v in extras.misc.items():
                    L.append(f"| misc.{k} | {v} |")
        except Exception:
            L.append(f"| (raw) | {extras} |")
        L.append("")
    else:
        L.append("(无 extras 数据)")
        L.append("")

    # ===== 8. Decision =====
    L.append("## 8. 综合决策详情")
    L.append("")
    if decision:
        L.append("### 8.1 完整评分卡")
        L.append("")
        L.append("| 维度 | 得分 | 说明 |")
        L.append("|------|------|------|")
        for k, v in decision.scorecard.items():
            note = ""
            if k == "overall":
                note = "加权总分 (决策依据)"
            elif k.endswith("_adj"):
                note = "NACS调节项"
            elif k == "base_avg":
                note = "7 Agent等权均分"
            L.append(f"| {k} | {v:.1f} | {note} |")
        L.append("")

        L.append("### 8.2 支持参与的理由")
        L.append("")
        for i, r in enumerate(decision.key_reasons_for, 1):
            L.append(f"{i}. {r}")
        L.append("")

        L.append("### 8.3 反对参与的理由")
        L.append("")
        for i, r in enumerate(decision.key_reasons_against, 1):
            L.append(f"{i}. {r}")
        L.append("")

        if decision.trigger_rules:
            L.append("### 8.4 后续触发规则")
            L.append("")
            L.append("| 条件 | 动作 | 严重性 |")
            L.append("|------|------|--------|")
            for tr in decision.trigger_rules:
                L.append(f"| {tr.condition} | {tr.action} | {tr.severity.value} |")
            L.append("")

        if decision.expected_return_6m:
            L.append("### 8.5 预期回报分布")
            L.append("")
            L.append("| 维度 | P10 | P25 | P50 | P75 | P90 |")
            L.append("|------|-----|-----|-----|-----|-----|")
            r6 = decision.expected_return_6m
            L.append(f"| 6个月 | {r6.p10:,.0f} | {r6.p25:,.0f} | {r6.p50:,.0f} | {r6.p75:,.0f} | {r6.p90:,.0f} |")
            if decision.expected_return_12m:
                r12 = decision.expected_return_12m
                L.append(f"| 12个月 | {r12.p10:,.0f} | {r12.p25:,.0f} | {r12.p50:,.0f} | {r12.p75:,.0f} | {r12.p90:,.0f} |")
            L.append("")

    # ===== 9. Cost =====
    L.append("## 9. LLM调用明细")
    L.append("")
    L.append("| # | Agent | Model | Input Tok | Output Tok | Cost | Runtime | Request ID |")
    L.append("|---|-------|-------|-----------|-----------|------|---------|------------|")
    for i, r in enumerate(cost_log.records, 1):
        L.append(
            f"| {i} | {r.agent_role or '-'} | {r.model} | "
            f"{r.tokens_input:,} | {r.tokens_output:,} | "
            f"${r.cost_usd:.4f} | {r.runtime_seconds:.1f}s | "
            f"`{(r.request_id or '-')[-12:]}`|"
        )
    L.append("")

    total_input = sum(r.tokens_input for r in cost_log.records)
    total_output = sum(r.tokens_output for r in cost_log.records)
    L.append(
        f"**合计**: {len(cost_log.records)} 次调用, "
        f"{total_input:,} input + {total_output:,} output = {total_input + total_output:,} tokens, "
        f"${cost_log.total_usd():.4f}"
    )
    L.append("")

    # ===== Footer =====
    L.append("---")
    L.append("")
    L.append("*本报告由多Agent LLM系统自动生成*  ")
    L.append("*Pipeline: PyMuPDF → Chunker → KIMI Extractor → 7 Expert Agents → Valuation Ensemble → Bull-Bear-Devil Debate → Synthesizer*  ")
    L.append("*所有分析基于招股书文本 + InMemory模式（无外部市场数据源）*")

    path.write_text("\n".join(L), encoding="utf-8")
    return path
