"""
HTML IC memo renderer — analyze_deal / case_review 的 --html 输出.

设计:
    - 单文件输出 (CSS 嵌入 <style>), 邮件附件即可分发
    - 模板用 Jinja2, 所有 escape 自动 (autoescape=True)
    - 数据结构与 _print_*_report 共用 (从同样 dict 渲染)
    - 没有外部 CSS / JS 依赖, 不联网

公开 API:
    render_single_deal(records, snap, asof, similar_cases) -> str
    render_compare(deals_results, snap, similar_per_deal) -> str
    render_case_review(report) -> str

每个返回完整 HTML 文档 (含 <!DOCTYPE html>); 调用方负责写文件.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


_HERE = Path(__file__).resolve().parent
_TEMPLATE_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"


# =============================================================================
# Jinja2 environment (singleton, lazy)
# =============================================================================

_ENV = None


def _get_env():
    """Lazy-init Jinja2 environment with autoescape and custom filters."""
    global _ENV
    if _ENV is not None:
        return _ENV
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as e:
        raise ImportError(
            "HTML 渲染需要 jinja2: pip install jinja2"
        ) from e
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "html.j2", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pct"] = _filter_pct
    env.filters["num"] = _filter_num
    env.filters["ret_class"] = _filter_ret_class
    env.filters["bar_pct"] = _filter_bar_pct
    env.filters["json_dump"] = _filter_json_dump
    env.filters["fmt_date"] = _filter_fmt_date
    _ENV = env
    return env


# =============================================================================
# Filters (用在模板里)
# =============================================================================

def _filter_pct(v: Optional[float], digits: int = 2) -> str:
    """0.123 -> '+12.30%'; None -> 'n/a'."""
    if v is None:
        return "n/a"
    try:
        return f"{v:+.{digits}%}"
    except (TypeError, ValueError):
        return "n/a"


def _filter_num(v: Optional[float], digits: int = 4) -> str:
    if v is None:
        return "n/a"
    try:
        if isinstance(v, (int,)) and not isinstance(v, bool):
            return f"{v:d}"
        return f"{v:.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _filter_ret_class(v: Optional[float]) -> str:
    """For return values, return CSS class name."""
    if v is None:
        return "ret-pending"
    try:
        if v > 0:
            return "ret-pos"
        if v < 0:
            return "ret-neg"
        return "ret-neutral"
    except (TypeError, ValueError):
        # Jinja2 Undefined / non-numeric → 当 pending 处理
        return "ret-pending"


def _filter_bar_pct(v: Optional[float], scale: float = 1.0) -> int:
    """Return bar fill % (0..100) clipped."""
    if v is None:
        return 0
    pct = max(0.0, min(1.0, v / scale)) * 100
    return int(round(pct))


def _filter_json_dump(v: Any) -> str:
    """Pretty-print JSON for <pre> blocks."""
    return json.dumps(_to_jsonable(v), ensure_ascii=False, indent=2,
                      default=str, sort_keys=True)


def _filter_fmt_date(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:10]


def _to_jsonable(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if hasattr(obj, "value") and hasattr(obj, "name"):  # Enum
        return obj.value
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


# =============================================================================
# CSS embedding
# =============================================================================

def _load_css() -> str:
    css = (_STATIC_DIR / "report.css").read_text(encoding="utf-8")
    return css


# =============================================================================
# Public renderers
# =============================================================================

def render_single_deal(records: List[Dict[str, Any]],
                       snap: Dict[str, Any],
                       asof: date,
                       similar_cases: List[Dict[str, Any]],
                       *,
                       title: Optional[str] = None,
                       themes_bundle: Optional[Dict[str, Any]] = None,
                       ipo_concept_names: Optional[List[str]] = None,
                       ai_revenue_pct_override: Optional[float] = None) -> str:
    """渲染单 deal IC memo (含 --price-scan 多场景).

    records: list of scenario dicts from analyze_deal._evaluate_deal:
             [{stock_code, ipo_id, row, scenario, price, offering, result}, ...]
    snap:    dict from panel_snapshots row
    similar_cases: list from find_similar_cases()

    S3 新增 (themes 接管):
        themes_bundle:           reports.themes_data.load_all() 返回; 决定是否出 theme panel
        ipo_concept_names:       传给 classifier
        ai_revenue_pct_override: 传给 thesis 的 ai_revenue_pct_override
    """
    if not records:
        raise ValueError("records cannot be empty")

    # 主 record (mid/final) 用于综合 thesis
    from reports.thesis import synthesize_thesis
    main = next((r for r in records if r["scenario"] in ("mid", "final")),
                records[0])
    row = main["row"]
    # row 可能是 sqlite Row 或 mock; 用 hasattr 判定
    def _row_get(r, key):
        if hasattr(r, "keys") and key in r.keys():
            return r[key]
        try:
            return r[key]
        except (KeyError, IndexError):
            return None

    thesis = synthesize_thesis(
        main["result"],
        panel_snap=snap,
        similar_cases=similar_cases,
        themes_bundle=themes_bundle,
        stock_code=main.get("stock_code"),
        gics_l2=_row_get(row, "gics_l2"),
        ipo_concept_names=ipo_concept_names,
        company_name=_row_get(row, "company_name_zh"),
        ai_revenue_pct_override=ai_revenue_pct_override,
    )

    env = _get_env()
    tpl = env.get_template("ic_memo_single.html.j2")
    return tpl.render(
        css=_load_css(),
        records=[_simplify_record(r) for r in records],
        snap=_simplify_snap(snap),
        asof=asof,
        similar_cases=similar_cases,
        thesis=thesis,
        title=title or _make_title(records[0]),
        generated_at=datetime.now(),
    )


def render_compare(deals_results: Dict[str, List[Dict[str, Any]]],
                   snap: Dict[str, Any],
                   similar_per_deal: Dict[str, List[Dict[str, Any]]],
                   *,
                   title: Optional[str] = None) -> str:
    """渲染多 deal 横评 IC memo."""
    from reports.thesis import synthesize_thesis
    env = _get_env()
    tpl = env.get_template("ic_memo_compare.html.j2")
    simplified = {
        code: [_simplify_record(r) for r in recs]
        for code, recs in deals_results.items()
    }
    # 每只 deal 的 thesis (用 mid/final 那一档)
    theses = {}
    for code, recs in deals_results.items():
        main = next((r for r in recs if r["scenario"] in ("mid", "final")),
                    recs[0])
        theses[code] = synthesize_thesis(
            main["result"], panel_snap=snap,
            similar_cases=similar_per_deal.get(code, []),
        )
    return tpl.render(
        css=_load_css(),
        deals=simplified,
        snap=_simplify_snap(snap),
        similar_per_deal=similar_per_deal,
        theses=theses,
        title=title or f"IC Compare ({len(deals_results)} deals)",
        generated_at=datetime.now(),
    )


def render_case_review(report: Dict[str, Any], *,
                       title: Optional[str] = None) -> str:
    """渲染上市后复盘报告. report 是 case_review.review() 返回的 dict."""
    env = _get_env()
    tpl = env.get_template("case_review.html.j2")
    return tpl.render(
        css=_load_css(),
        report=report,
        title=title or f"Case Review · {report.get('stock_code')} "
                       f"{report.get('company_name_zh') or ''}",
        generated_at=datetime.now(),
    )


# =============================================================================
# Helpers: dict simplification (templates 不直接处理 NACSResult dataclass)
# =============================================================================

def _simplify_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """把 analyze_deal _evaluate_deal 返回的 record 转成模板友好的扁平 dict.

    rec 内的 result 是 NACSResult, offering 是 IPOOffering, row 是 sqlite Row.
    """
    r = rec["result"]
    row = rec["row"]
    offering = rec["offering"]

    # 拆 NACSResult
    l1 = getattr(r.layer1, "components", {}) or {}
    l2 = getattr(r.layer2, "components", {}) or {}
    l3 = getattr(r.layer3, "components", {}) or {}
    # 公开子项 (不带 _ 前缀)
    l1_public = {k: v for k, v in l1.items() if not k.startswith("_")}
    l2_public = {k: v for k, v in l2.items() if not k.startswith("_")}

    return {
        "scenario": rec["scenario"],
        "price": rec["price"],
        "stock_code": rec["stock_code"],
        "ipo_id": rec["ipo_id"],
        "name": row["company_name_zh"] if "company_name_zh" in row.keys() else None,
        "status": row["status"] if "status" in row.keys() else None,
        "listing_chapter": row["listing_chapter"] if "listing_chapter" in row.keys() else None,
        "gics_l2": row["gics_l2"] if "gics_l2" in row.keys() else None,
        "listing_date": row["listing_date"] if "listing_date" in row.keys() else None,
        "expected_listing_date": (
            row["expected_listing_date"]
            if "expected_listing_date" in row.keys() else None
        ),
        "nacs_raw": r.nacs_raw,
        "nacs_adjusted": r.nacs_adjusted,
        "Q_company": r.Q_company,
        "Q_ecosystem": r.Q_ecosystem,
        "R_lockup": r.R_lockup,
        "decision": r.decision,
        "position_pct": r.position_pct,
        "cluster_count": getattr(offering, "cluster_cornerstone_count", 0),
        "regime_score": getattr(offering, "regime_score", None),
        "layer1": {
            "raw_score": r.layer1.raw_score,
            "components": l1_public,
            "reasons": dict(r.layer1.reasons or {}),
        },
        "layer2": {
            "raw_score": r.layer2.raw_score,
            "components": l2_public,
            "reasons": dict(r.layer2.reasons or {}),
        },
        "layer3": {
            "components": l3,
            "reasons": dict(r.layer3.reasons or {}),
        },
        "adjustments_applied": list(r.adjustments_applied or []),
        "warnings": list(r.warnings or []),
        "decision_rationale": list(r.decision_rationale or []),
        # 调整解释 (单条 → 解读)
        "adjustment_explanations": [
            __import__("nacs_rationale").explain_adjustment(adj)
            for adj in (r.adjustments_applied or [])
        ],
        # offering 输入快照 (audit)
        "offering_inputs": _to_jsonable(offering),
    }


def _simplify_snap(snap: Dict[str, Any]) -> Dict[str, Any]:
    """从 panel_snapshots row 里挑模板需要的字段, 解 JSON."""
    if not snap:
        return {}
    out = {
        "snapshot_id": snap.get("snapshot_id"),
        "asof_date": snap.get("asof_date"),
        "n_ipos_in_universe": snap.get("n_ipos_in_universe"),
        "regime_score": snap.get("regime_score"),
        "config_version": snap.get("config_version"),
        "config_hash": snap.get("config_hash"),
        "code_git_sha": snap.get("code_git_sha"),
    }
    # market_env / aggregates 解出来给模板用
    for key in ("market_env_json", "aggregates_json"):
        v = snap.get(key)
        if v:
            try:
                out[key.replace("_json", "")] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                pass
    return out


def _make_title(rec: Dict[str, Any]) -> str:
    code = rec.get("stock_code") or "?"
    name = rec.get("name") or rec.get("row", {}).get("company_name_zh") if isinstance(rec.get("row"), dict) else None
    if hasattr(rec.get("row"), "keys"):  # sqlite3.Row
        try:
            name = rec["row"]["company_name_zh"]
        except (IndexError, KeyError):
            name = None
    if name:
        return f"IC Memo · {code} {name}"
    return f"IC Memo · {code}"


# =============================================================================
# Convenience: write to file
# =============================================================================

def write_html(html: str, path: Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p
