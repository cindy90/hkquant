"""
themes_data — 加载 themes/ 目录下的主题情绪 + AI 镀金溢价数据.

设计:
    每个 loader 返回 (data, provenance) 元组:
      data       — 解析后的 JSON/CSV 内容
      provenance — {path, mtime_iso, schema_version, asof, is_stale, ...}
                   写进 nacs_predictions.themes_provenance_json, 用于事后复盘
                   "当时这只 deal 的主题情绪数据是哪一天 fetch 的?"

    设计原则:
      1. 缺失/损坏不抛异常 — 返回 (None, provenance with status='missing')
         主流程 (analyze_deal) 应在 themes_data 任一字段为 None 时优雅降级,
         不阻断决策评分.
      2. 时鲜检查 — heat_today.json 的 as_of 距 today() > STALE_DAYS 时
         provenance.is_stale=True, 让模板/CLI 可显式 warn 用户.
      3. 不缓存 — 每次调 IO. analyze_deal 是单 deal CLI, 不需 perf 优化.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_THEMES_DIR = _PROJECT_ROOT / "themes"

# heat_today.as_of 距 today 超过 N 天 → is_stale=True
HEAT_STALE_DAYS = 3
# premium_curve.fitted_at 距 today 超过 N 天 → is_stale=True
CURVE_STALE_DAYS = 90


# =============================================================================
# Provenance 数据结构
# =============================================================================

@dataclass
class Provenance:
    """每次 load 都附带的审计元数据."""
    path: str                                       # 相对 PROJECT_ROOT 的路径
    status: str                                     # 'ok' / 'missing' / 'corrupt'
    mtime_iso: Optional[str] = None                 # 文件 mtime
    schema_version: Optional[str] = None
    asof: Optional[str] = None                      # 数据自称的 as-of date (内容字段)
    is_stale: bool = False
    stale_days_threshold: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# Helpers
# =============================================================================

def _file_mtime_iso(p: Path) -> Optional[str]:
    if not p.exists():
        return None
    return datetime.fromtimestamp(p.stat().st_mtime).isoformat()


def _safe_relpath(p: Path) -> str:
    try:
        return str(p.relative_to(_PROJECT_ROOT))
    except ValueError:
        return str(p)


def _check_freshness(asof_str: Optional[str], stale_days: int,
                     today: Optional[date] = None) -> Tuple[bool, Optional[int]]:
    """返回 (is_stale, days_old). asof 解析失败返回 (False, None)."""
    if not asof_str:
        return False, None
    try:
        asof = date.fromisoformat(str(asof_str)[:10])
    except (ValueError, TypeError):
        return False, None
    today = today or date.today()
    days_old = (today - asof).days
    return days_old > stale_days, days_old


# =============================================================================
# heat_today.json 加载
# =============================================================================

def load_heat_today(themes_dir: Optional[Path] = None,
                    today: Optional[date] = None
                    ) -> Tuple[Optional[Dict[str, Any]], Provenance]:
    """
    加载 themes/heat_today.json.

    返回:
        data:
            {
              "as_of": "2026-05-08",
              "themes": {
                "<theme_id>": {
                  "label": str,
                  "heat_score": int (0-100),
                  "ret_5d": float, "ret_20d": float, "ret_60d": float,
                  "pe_ttm_avg": float,
                  "reason": str,
                  "warning": str | None,
                  "source": str,  # 'kimi' / etc
                  ...
                }
              }
            }
        provenance: 含 is_stale (基于 as_of vs today; 超 HEAT_STALE_DAYS 为 stale)
    """
    p = (themes_dir or DEFAULT_THEMES_DIR) / "heat_today.json"
    relp = _safe_relpath(p)
    if not p.exists():
        return None, Provenance(path=relp, status="missing")
    try:
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as e:
        return None, Provenance(
            path=relp, status="corrupt",
            mtime_iso=_file_mtime_iso(p),
            notes=[f"{type(e).__name__}: {e}"],
        )
    asof = data.get("as_of") if isinstance(data, dict) else None
    is_stale, days_old = _check_freshness(asof, HEAT_STALE_DAYS, today)
    notes = []
    if is_stale and days_old is not None:
        notes.append(f"heat_today.as_of 距 today 已 {days_old} 天 "
                     f"(>{HEAT_STALE_DAYS} 阈值)")
    return data, Provenance(
        path=relp, status="ok",
        mtime_iso=_file_mtime_iso(p),
        asof=asof,
        is_stale=is_stale,
        stale_days_threshold=HEAT_STALE_DAYS,
        notes=notes,
    )


# =============================================================================
# premium_curve.json 加载
# =============================================================================

def load_premium_curve(themes_dir: Optional[Path] = None,
                       today: Optional[date] = None
                       ) -> Tuple[Optional[Dict[str, Any]], Provenance]:
    """
    加载 themes/premium_curve.json.

    返回:
        data:
            {
              "fitted_at": ISO timestamp,
              "as_of_data": "2026-05-08",
              "n_samples_total": 36, "n_samples_used": 31,
              "model": "log_linear: y = a * log(1 + b*x) + c",
              "params": {"a": 5.17, "b": 0.5, "c": -0.23},
              "r_squared": 0.39,
              "note": str,
              "lookup_table": [{"ai_pct": float, "premium": float}, ...],
              "samples": [{"code", "ai_revenue_pct", "premium", ...}, ...]
            }
        provenance: 含 is_stale (基于 as_of_data vs today; 超 90 天为 stale)
    """
    p = (themes_dir or DEFAULT_THEMES_DIR) / "premium_curve.json"
    relp = _safe_relpath(p)
    if not p.exists():
        return None, Provenance(path=relp, status="missing")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, Provenance(
            path=relp, status="corrupt",
            mtime_iso=_file_mtime_iso(p),
            notes=[f"{type(e).__name__}: {e}"],
        )
    asof = data.get("as_of_data") if isinstance(data, dict) else None
    is_stale, days_old = _check_freshness(asof, CURVE_STALE_DAYS, today)
    notes = []
    if isinstance(data, dict):
        r2 = data.get("r_squared")
        if r2 is not None and r2 < 0.30:
            notes.append(f"premium_curve.r_squared={r2:.2f} 偏低, "
                         f"溢价估计置信度有限")
        n_used = data.get("n_samples_used", 0)
        if n_used < 20:
            notes.append(f"n_samples_used={n_used}<20, 拟合样本量偏少")
    if is_stale and days_old is not None:
        notes.append(f"premium_curve 已 {days_old} 天没重拟; 建议跑 "
                     f"themes/research_premium_coefficient.py")
    return data, Provenance(
        path=relp, status="ok",
        mtime_iso=_file_mtime_iso(p),
        asof=asof,
        is_stale=is_stale,
        stale_days_threshold=CURVE_STALE_DAYS,
        notes=notes,
    )


# =============================================================================
# theme_definitions.json 加载
# =============================================================================

def load_theme_definitions(themes_dir: Optional[Path] = None
                           ) -> Tuple[Optional[Dict[str, Any]], Provenance]:
    """
    加载 themes/theme_definitions.json.

    返回:
        data:
            {
              "_schema_version": "1.0",
              "_last_updated": "2026-05-08",
              "themes": {
                "<theme_id>": {
                  "label": str,
                  "iv_bkid": str,
                  "fallback_quote_code": str,
                  "core_companies": [{"code","name","role"}, ...],
                  "keywords": [str, ...]
                }
              }
            }
        provenance: 仅记录 path / mtime / schema_version (不做时鲜检查;
                    主题字典是配置, 半年级别的稳定档)
    """
    p = (themes_dir or DEFAULT_THEMES_DIR) / "theme_definitions.json"
    relp = _safe_relpath(p)
    if not p.exists():
        return None, Provenance(path=relp, status="missing")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, Provenance(
            path=relp, status="corrupt",
            mtime_iso=_file_mtime_iso(p),
            notes=[f"{type(e).__name__}: {e}"],
        )
    sv = data.get("_schema_version") if isinstance(data, dict) else None
    return data, Provenance(
        path=relp, status="ok",
        mtime_iso=_file_mtime_iso(p),
        schema_version=sv,
        asof=data.get("_last_updated") if isinstance(data, dict) else None,
    )


# =============================================================================
# ai_revenue_manual.json 加载
# =============================================================================

def load_ai_revenue_manual(themes_dir: Optional[Path] = None
                           ) -> Tuple[Optional[Dict[str, float]], Provenance]:
    """
    加载 themes/ai_revenue_manual.json, 返回 {stock_code: ai_revenue_pct} dict.

    needs_review=true 的样本会被排除 (跟 research_premium_coefficient.py 一致).
    """
    p = (themes_dir or DEFAULT_THEMES_DIR) / "ai_revenue_manual.json"
    relp = _safe_relpath(p)
    if not p.exists():
        return None, Provenance(path=relp, status="missing")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, Provenance(
            path=relp, status="corrupt",
            mtime_iso=_file_mtime_iso(p),
            notes=[f"{type(e).__name__}: {e}"],
        )

    # 港股代码规范化: 跟 classify_deal_to_theme 一致, 去前导 0 统一为 4 位主流形式
    def _canon(c: str) -> str:
        if not c:
            return ""
        c = c.upper().strip()
        if "." in c:
            num, suffix = c.split(".", 1)
            return f"{num.lstrip('0') or '0'}.{suffix}"
        return c

    out: Dict[str, float] = {}
    n_skipped_review = 0
    samples = raw.get("samples", []) if isinstance(raw, dict) else []
    for s in samples:
        if not isinstance(s, dict):
            continue
        code = _canon(s.get("code", ""))
        pct = s.get("ai_revenue_pct")
        if not code or pct is None:
            continue
        if s.get("needs_review"):
            n_skipped_review += 1
            continue
        try:
            out[code] = float(pct)
        except (TypeError, ValueError):
            continue

    notes = [f"loaded {len(out)} samples; skipped {n_skipped_review} "
             f"needs_review=true"]
    return out, Provenance(
        path=relp, status="ok",
        mtime_iso=_file_mtime_iso(p),
        schema_version=raw.get("_schema_version") if isinstance(raw, dict) else None,
        notes=notes,
    )


# =============================================================================
# history.csv 加载
# =============================================================================

def load_history(themes_dir: Optional[Path] = None,
                 last_n_days: int = 90
                 ) -> Tuple[Optional[Dict[str, List[Tuple[str, int]]]], Provenance]:
    """
    加载 themes/history.csv, 返回 {theme_id: [(date_str, heat_score), ...]}.

    last_n_days: 只保留最近 N 天 (按 date 列倒序后取); IC memo sparkline 用 30d.
    """
    p = (themes_dir or DEFAULT_THEMES_DIR) / "history.csv"
    relp = _safe_relpath(p)
    if not p.exists():
        return None, Provenance(path=relp, status="missing")
    try:
        with p.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        return None, Provenance(
            path=relp, status="corrupt",
            mtime_iso=_file_mtime_iso(p),
            notes=[f"{type(e).__name__}: {e}"],
        )
    if not rows:
        return {}, Provenance(
            path=relp, status="ok",
            mtime_iso=_file_mtime_iso(p),
            notes=["history.csv 为空 (theme_tracker.py 还没累积过)"],
        )

    # 按 date 升序排
    rows.sort(key=lambda r: r.get("date", ""))
    if last_n_days > 0:
        rows = rows[-last_n_days:]

    theme_ids = [k for k in rows[0].keys() if k != "date"]
    out: Dict[str, List[Tuple[str, int]]] = {tid: [] for tid in theme_ids}
    for r in rows:
        d = r.get("date", "")
        for tid in theme_ids:
            v = r.get(tid)
            if v is not None and v != "":
                try:
                    out[tid].append((d, int(float(v))))
                except (TypeError, ValueError):
                    continue

    return out, Provenance(
        path=relp, status="ok",
        mtime_iso=_file_mtime_iso(p),
        asof=rows[-1].get("date") if rows else None,
        notes=[f"loaded {len(rows)} rows × {len(theme_ids)} themes"],
    )


# =============================================================================
# classify_deal_to_theme — 把一个 deal 映射到 theme_id
# =============================================================================

@dataclass
class ClassificationResult:
    """Deal → theme_id 的判定 + 来源 (审计用)."""
    theme_id: Optional[str]                          # None = 没匹配
    confidence: str                                  # 'high' / 'medium' / 'low' / 'none'
    match_reason: str                                # 人类可读的"为什么命中这个主题"
    matched_signals: List[Dict[str, str]] = field(default_factory=list)
                                                     # [{"signal": "core_company", "value": "...", "theme_id": "..."}, ...]
    candidates: List[Dict[str, Any]] = field(default_factory=list)
                                                     # 所有打分>0 的主题, 按分降序

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify_deal_to_theme(stock_code: str,
                           gics_l2: Optional[str] = None,
                           ipo_concept_names: Optional[List[str]] = None,
                           company_name: Optional[str] = None,
                           theme_definitions: Optional[Dict[str, Any]] = None
                           ) -> ClassificationResult:
    """
    把一个 deal 推断到 themes/theme_definitions.json 里的 theme_id.

    匹配 3 层 (按 confidence 降序):
        high   — stock_code 直接命中某主题的 core_companies (强证据: 同业)
        medium — gics_l2 / ipo_concept_names / company_name 包含主题 keyword 之一
        low    — 同上但只命中 1 个 keyword 且与 core_companies 完全无关
        none   — 都没命中

    设计原则:
        1. **可追溯**: matched_signals 记录每条命中的 (signal, value, theme),
           可写进 nacs_predictions 复盘
        2. **多匹配选最强**: 按命中信号数 + confidence 排序
        3. **不依赖 LLM**: 纯字符串匹配, 100% 可重复

    Args:
        stock_code: 拟上市代码, e.g. "1187.HK"
        gics_l2: ipo_master.gics_l2, e.g. "资讯科技业(HS)-..."
        ipo_concept_names: ipo_concepts 表的 concept_name 列表
        company_name: 公司中文名
        theme_definitions: load_theme_definitions() 的 data; 为 None 时返回 none

    Returns:
        ClassificationResult
    """
    if not theme_definitions or "themes" not in theme_definitions:
        return ClassificationResult(
            theme_id=None, confidence="none",
            match_reason="theme_definitions 未加载",
        )

    themes = theme_definitions["themes"]
    # 各主题候选 score
    scores: Dict[str, int] = {tid: 0 for tid in themes}
    matched_signals: Dict[str, List[Dict[str, str]]] = {tid: [] for tid in themes}

    # 港股代码规范化: 主流 ipo_master 用 4-digit 'XXXX.HK',
    # theme_definitions/core_companies 多为 5-digit 'XXXXX.HK' (港交所标准).
    # 统一去前导 0 后比较, 避免 0992 vs 00992 不匹配.
    def _canon_code(c: str) -> str:
        if not c:
            return ""
        c = c.upper().strip()
        if "." in c:
            num, suffix = c.split(".", 1)
            return f"{num.lstrip('0') or '0'}.{suffix}"
        return c

    code_normalized = _canon_code(stock_code or "")
    haystack_parts = []
    if gics_l2:
        haystack_parts.append(gics_l2)
    if ipo_concept_names:
        haystack_parts.extend(ipo_concept_names)
    if company_name:
        haystack_parts.append(company_name)
    haystack = " | ".join(str(s) for s in haystack_parts if s)

    # ---- Layer 1: core_companies hit (high confidence, 权重 +10) ----
    for tid, td in themes.items():
        for cc in td.get("core_companies", []) or []:
            cc_code = (cc.get("code") if isinstance(cc, dict) else None) or ""
            if _canon_code(cc_code) == code_normalized and code_normalized:
                scores[tid] += 10
                matched_signals[tid].append({
                    "signal": "core_company",
                    "value": f"{cc_code} ({cc.get('name', '')})",
                    "theme_id": tid,
                })

    # ---- Layer 2: keyword in (gics_l2 / concept_name / company_name) (+3 each) ----
    for tid, td in themes.items():
        for kw in td.get("keywords", []) or []:
            if not kw:
                continue
            if kw in haystack:
                scores[tid] += 3
                matched_signals[tid].append({
                    "signal": "keyword",
                    "value": kw,
                    "theme_id": tid,
                })

    # 决定 winner
    best_tid = max(scores, key=lambda t: scores[t])
    best_score = scores[best_tid]
    if best_score == 0:
        return ClassificationResult(
            theme_id=None, confidence="none",
            match_reason=f"haystack='{haystack[:80]}' 不命中任何 theme keyword 或 core_company",
            candidates=[],
        )

    # confidence 分级
    has_core = any(s["signal"] == "core_company"
                   for s in matched_signals[best_tid])
    n_keywords = sum(1 for s in matched_signals[best_tid]
                     if s["signal"] == "keyword")
    if has_core:
        conf = "high"
    elif n_keywords >= 2:
        conf = "medium"
    elif n_keywords == 1:
        conf = "low"
    else:
        conf = "none"

    # 人类可读 reason
    parts = []
    if has_core:
        cc_hits = [s["value"] for s in matched_signals[best_tid]
                   if s["signal"] == "core_company"]
        parts.append(f"core_companies 命中: {', '.join(cc_hits)}")
    if n_keywords > 0:
        kw_hits = [s["value"] for s in matched_signals[best_tid]
                   if s["signal"] == "keyword"]
        parts.append(f"keywords 命中 {n_keywords} 个: {', '.join(kw_hits[:5])}")
    label = themes[best_tid].get("label", best_tid)
    reason = f"主题=[{label}] · score={best_score} · " + " · ".join(parts)

    # 排序候选 (score>0 的所有主题, 给上层看其它备选)
    candidates = sorted(
        [{"theme_id": t, "score": s,
          "label": themes[t].get("label", t),
          "n_signals": len(matched_signals[t])}
         for t, s in scores.items() if s > 0],
        key=lambda x: -x["score"],
    )

    return ClassificationResult(
        theme_id=best_tid,
        confidence=conf,
        match_reason=reason,
        matched_signals=matched_signals[best_tid],
        candidates=candidates,
    )


# =============================================================================
# Bundled load_all helper — 单次调用拿全
# =============================================================================

def load_all(themes_dir: Optional[Path] = None,
             today: Optional[date] = None
             ) -> Dict[str, Any]:
    """
    一次性 load 5 个 themes 文件, 返回:
        {
          "heat_today": (data, provenance),
          "premium_curve": (data, provenance),
          "theme_definitions": (data, provenance),
          "ai_revenue_manual": (data, provenance),
          "history": (data, provenance),
        }

    任一失败不抛异常; 上层 (analyze_deal / synthesize_thesis) 要 graceful 降级.
    """
    return {
        "heat_today": load_heat_today(themes_dir, today),
        "premium_curve": load_premium_curve(themes_dir, today),
        "theme_definitions": load_theme_definitions(themes_dir),
        "ai_revenue_manual": load_ai_revenue_manual(themes_dir),
        "history": load_history(themes_dir),
    }
