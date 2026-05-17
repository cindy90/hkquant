"""
NACS 时序交叉验证回测 — 过拟合防护框架

用法:
    python run_cv_backtest.py
    python run_cv_backtest.py --config configs/nacs_v8.yaml
    python run_cv_backtest.py --workers 4
    python run_cv_backtest.py --report-md reports/cv.md
    python run_cv_backtest.py --skip-train-ic

设计:
    Anchored Expanding Window — 训练集始终从 2022-01 起, 测试窗口向前滑动.
    6 折半年一折, 每折独立过滤 history (防泄露), 计算 OOS IC + 过拟合诊断.

    不修改任何现有文件, 复用 run_v7_backtest.py 的评分函数.
"""
import argparse
import json
import math
import sys
import sqlite3
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Windows UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd

# Reuse scoring functions from run_v7_backtest
from run_v7_backtest import (
    db_connect,
    score_one_ipo,
    parallel_score_ipos,
    ic,
    long_short,
)


# =============================================================================
# Data structures
# =============================================================================

@dataclass
class FoldSpec:
    """One fold of the anchored expanding window CV."""
    fold_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass
class FoldResult:
    """Results for a single fold."""
    fold_id: int
    train_range: Tuple[str, str]
    test_range: Tuple[str, str]
    train_n: int
    test_n: int
    test_ic: Dict[str, Any] = field(default_factory=dict)
    train_ic: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Default fold boundaries (6 folds, ~half-year each)
# =============================================================================

DEFAULT_BOUNDARIES = [
    # (train_start, train_end, test_start, test_end)
    (date(2022, 1, 1), date(2023, 6, 30), date(2023, 7, 1), date(2023, 12, 31)),
    (date(2022, 1, 1), date(2023, 12, 31), date(2024, 1, 1), date(2024, 6, 30)),
    (date(2022, 1, 1), date(2024, 6, 30), date(2024, 7, 1), date(2024, 12, 31)),
    (date(2022, 1, 1), date(2024, 12, 31), date(2025, 1, 1), date(2025, 6, 30)),
    (date(2022, 1, 1), date(2025, 6, 30), date(2025, 7, 1), date(2025, 12, 31)),
    (date(2022, 1, 1), date(2025, 12, 31), date(2026, 1, 1), date(2026, 5, 31)),
]


def build_fold_specs(boundaries=None) -> List[FoldSpec]:
    """Build fold specifications from boundary tuples.

    Args:
        boundaries: list of (train_start, train_end, test_start, test_end) tuples.
                    None => use DEFAULT_BOUNDARIES.

    Returns:
        List of FoldSpec with anchored expanding windows.
    """
    if boundaries is None:
        boundaries = DEFAULT_BOUNDARIES
    folds = []
    for i, (ts, te, vs, ve) in enumerate(boundaries):
        folds.append(FoldSpec(
            fold_id=i,
            train_start=ts,
            train_end=te,
            test_start=vs,
            test_end=ve,
        ))
    return folds


def parse_custom_folds(spec_str: str) -> List[Tuple[date, date, date, date]]:
    """Parse custom fold boundaries from CLI string.

    Format: "train_start:train_end:test_start:test_end,..." (ISO dates)
    Example: "2022-01-01:2023-06-30:2023-07-01:2023-12-31,..."
    """
    boundaries = []
    for fold_str in spec_str.split(","):
        parts = fold_str.strip().split(":")
        if len(parts) != 4:
            raise ValueError(
                f"Each fold needs 4 dates (train_start:train_end:test_start:test_end), "
                f"got {len(parts)}: {fold_str}"
            )
        dates = [date.fromisoformat(p.strip()) for p in parts]
        boundaries.append(tuple(dates))
    return boundaries


# =============================================================================
# History filtering (anti-leakage)
# =============================================================================

def filter_history_for_fold(
    history: List[Tuple[Optional[date], Optional[float]]],
    train_end: date,
) -> List[Tuple[Optional[date], Optional[float]]]:
    """Filter history to only include IPOs within the training period.

    This is the core anti-leakage mechanism: regime score computation
    must not see future IPO performance data.

    Args:
        history: full [(listing_date, return_d30), ...] from DB
        train_end: end date of the training period

    Returns:
        Filtered history with only entries where date <= train_end.
        Entries with date=None are excluded (cannot verify non-leakage).
    """
    return [
        (d, r) for d, r in history
        if d is not None and d <= train_end
    ]


# =============================================================================
# IC computation for a fold
# =============================================================================

def compute_fold_ic(records: List[Dict]) -> Dict[str, Any]:
    """Compute IC and long-short metrics for a set of scored IPO records.

    Args:
        records: list of dicts from score_one_ipo, each with
                 'NACS', 'r5d', 'r30d', 'r60d', 'r180d'.

    Returns:
        dict mapping horizon key to {ic, n, ls_spread, ls_t_stat}.
    """
    if not records:
        return {}

    df = pd.DataFrame(records)
    result = {}
    for col, key in [("r5d", "5d"), ("r30d", "30d"), ("r60d", "60d"), ("r180d", "180d")]:
        if col not in df.columns:
            continue
        ic_val, n = ic(df["NACS"], df[col])
        ls = long_short(df["NACS"].values, df[col].values)
        result[key] = {
            "ic": None if (isinstance(ic_val, float) and math.isnan(ic_val)) else float(ic_val),
            "n": int(n),
            "ls_spread": float(ls["spread"]) if ls else None,
            "ls_t_stat": float(ls["t_stat"]) if ls else None,
        }
    return result


# =============================================================================
# Single fold runner
# =============================================================================

def run_one_fold(
    fold: FoldSpec,
    full_history: List[Tuple[Optional[date], Optional[float]]],
    all_ipo_rows: List[dict],
    db_path: str,
    *,
    workers: int = 1,
    use_static_env: bool = False,
    config_path: Optional[str] = None,
    skip_train_ic: bool = False,
) -> FoldResult:
    """Run scoring for one CV fold.

    Args:
        fold: FoldSpec defining train/test boundaries
        full_history: complete IPO history from DB
        all_ipo_rows: list of dicts with ipo_id, listing_date, pricing_date
        db_path: path to SQLite DB
        workers: parallel workers for scoring
        use_static_env: use static MarketEnvironment
        config_path: optional NacsConfig YAML path
        skip_train_ic: skip computing training set IC

    Returns:
        FoldResult with IC metrics for train and test sets.
    """
    # Filter history for this fold (anti-leakage)
    train_history = filter_history_for_fold(full_history, fold.train_end)

    # Split IPOs into train and test sets by listing_date
    train_ids = []
    test_ids = []
    for row in all_ipo_rows:
        ld = row["listing_date"]
        if ld is None:
            continue
        if isinstance(ld, str):
            ld = date.fromisoformat(ld[:10])
        if fold.train_start <= ld <= fold.train_end:
            train_ids.append(row["ipo_id"])
        elif fold.test_start <= ld <= fold.test_end:
            test_ids.append(row["ipo_id"])

    # Score test set (always using train-filtered history)
    test_records, test_errors = parallel_score_ipos(
        db_path=db_path,
        ipo_ids=test_ids,
        history=train_history,
        workers=workers,
        use_static_env=use_static_env,
        config_path=config_path,
    )
    test_ic = compute_fold_ic(test_records)

    # Score train set (optional, for overfit ratio)
    train_ic = {}
    train_n = len(train_ids)
    if not skip_train_ic and train_ids:
        train_records, _ = parallel_score_ipos(
            db_path=db_path,
            ipo_ids=train_ids,
            history=train_history,
            workers=workers,
            use_static_env=use_static_env,
            config_path=config_path,
        )
        train_ic = compute_fold_ic(train_records)
        train_n = len(train_records)

    return FoldResult(
        fold_id=fold.fold_id,
        train_range=(fold.train_start.isoformat(), fold.train_end.isoformat()),
        test_range=(fold.test_start.isoformat(), fold.test_end.isoformat()),
        train_n=train_n,
        test_n=len(test_records),
        test_ic=test_ic,
        train_ic=train_ic,
    )


# =============================================================================
# Overfit diagnostics
# =============================================================================

def compute_overfit_metrics(
    fold_results: List[FoldResult],
    full_sample_ic: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute overfit diagnostics from fold results.

    Args:
        fold_results: list of FoldResult from all folds
        full_sample_ic: IC dict from full-sample backtest (for overfit_ratio).
                        If None, overfit_ratio is not computed.

    Returns:
        dict with overfit diagnostics and verdict.
    """
    # Collect OOS IC values per horizon
    oos_ics: Dict[str, List[float]] = {}
    train_ics: Dict[str, List[float]] = {}

    for fr in fold_results:
        for key in ("30d", "60d", "180d"):
            if key in fr.test_ic and fr.test_ic[key]["ic"] is not None:
                oos_ics.setdefault(key, []).append(fr.test_ic[key]["ic"])
            if key in fr.train_ic and fr.train_ic[key]["ic"] is not None:
                train_ics.setdefault(key, []).append(fr.train_ic[key]["ic"])

    result: Dict[str, Any] = {}

    # Per-horizon metrics
    for key in ("30d", "60d", "180d"):
        oos = oos_ics.get(key, [])
        if not oos:
            continue

        mean_oos = float(np.mean(oos))
        std_oos = float(np.std(oos, ddof=1)) if len(oos) > 1 else 0.0
        worst_fold = float(min(oos))
        n_negative = sum(1 for v in oos if v < 0)

        result[f"ic_mean_oos_{key}"] = mean_oos
        result[f"ic_std_oos_{key}"] = std_oos
        result[f"worst_fold_ic_{key}"] = worst_fold
        result[f"n_folds_negative_{key}"] = n_negative

        # IC degradation (train - OOS)
        train = train_ics.get(key, [])
        if train:
            mean_train = float(np.mean(train))
            result[f"ic_degradation_{key}"] = mean_train - mean_oos
        else:
            result[f"ic_degradation_{key}"] = None

        # Overfit ratio (full_sample / mean_OOS)
        if full_sample_ic and key in full_sample_ic:
            fs_ic = full_sample_ic[key].get("ic")
            if fs_ic is not None and abs(mean_oos) > 1e-6:
                result[f"overfit_ratio_{key}"] = fs_ic / mean_oos
            else:
                result[f"overfit_ratio_{key}"] = None
        else:
            result[f"overfit_ratio_{key}"] = None

    # Verdict (based on 60d, fallback to 30d)
    verdict_key = "60d" if f"ic_mean_oos_60d" in result else "30d"
    result["verdict"] = _compute_verdict(result, verdict_key, fold_results)

    return result


def _compute_verdict(
    metrics: Dict[str, Any],
    key: str,
    fold_results: List[FoldResult],
) -> str:
    """Determine overall verdict: PASS / WARNING / OVERFIT.

    Rules:
        OVERFIT if:
            - overfit_ratio > 2.0, OR
            - more than half of folds have negative OOS IC
        WARNING if:
            - overfit_ratio in [1.5, 2.0], OR
            - ic_degradation > 0.10
        PASS otherwise
    """
    n_folds = len(fold_results)
    n_neg = metrics.get(f"n_folds_negative_{key}", 0)
    overfit_ratio = metrics.get(f"overfit_ratio_{key}")
    degradation = metrics.get(f"ic_degradation_{key}")

    # OVERFIT checks
    if n_folds > 0 and n_neg > n_folds / 2:
        return "OVERFIT"
    if overfit_ratio is not None and overfit_ratio > 2.0:
        return "OVERFIT"

    # WARNING checks
    if overfit_ratio is not None and overfit_ratio > 1.5:
        return "WARNING"
    if degradation is not None and degradation > 0.10:
        return "WARNING"

    return "PASS"


# =============================================================================
# Full-sample IC (for overfit ratio baseline)
# =============================================================================

def run_full_sample_ic(
    all_ipo_rows: List[dict],
    full_history: List[Tuple[Optional[date], Optional[float]]],
    db_path: str,
    *,
    workers: int = 1,
    use_static_env: bool = False,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run full-sample scoring and return IC dict (same as run_v7_backtest)."""
    ipo_ids = [r["ipo_id"] for r in all_ipo_rows]
    records, _ = parallel_score_ipos(
        db_path=db_path,
        ipo_ids=ipo_ids,
        history=full_history,
        workers=workers,
        use_static_env=use_static_env,
        config_path=config_path,
    )
    return compute_fold_ic(records)


# =============================================================================
# Report generation
# =============================================================================

def print_fold_report(fold_results: List[FoldResult], overfit: Dict[str, Any]):
    """Print fold-by-fold results to console."""
    print("\n" + "=" * 70)
    print("NACS Cross-Validation Report (Anchored Expanding Window)")
    print("=" * 70)

    for fr in fold_results:
        print(f"\nFold {fr.fold_id}: "
              f"train=[{fr.train_range[0]}, {fr.train_range[1]}] (n={fr.train_n}), "
              f"test=[{fr.test_range[0]}, {fr.test_range[1]}] (n={fr.test_n})")

        if fr.test_ic:
            parts = []
            for key in ("30d", "60d", "180d"):
                if key in fr.test_ic and fr.test_ic[key]["ic"] is not None:
                    ic_val = fr.test_ic[key]["ic"]
                    ls_spread = fr.test_ic[key].get("ls_spread")
                    ls_t = fr.test_ic[key].get("ls_t_stat")
                    part = f"{key} ic={ic_val:+.4f}"
                    if ls_spread is not None:
                        part += f" L-S={ls_spread:+.1%}"
                    if ls_t is not None:
                        part += f" t={ls_t:+.2f}"
                    parts.append(part)
            if parts:
                print(f"  OOS  {' | '.join(parts)}")

        if fr.train_ic:
            parts = []
            for key in ("30d", "60d", "180d"):
                if key in fr.train_ic and fr.train_ic[key]["ic"] is not None:
                    parts.append(f"{key} ic={fr.train_ic[key]['ic']:+.4f}")
            if parts:
                print(f"  IS   {' | '.join(parts)}")

    # Overfit diagnostics
    print(f"\n{'=' * 70}")
    print("Overfit Diagnostics:")
    for key in ("30d", "60d", "180d"):
        mean_key = f"ic_mean_oos_{key}"
        ratio_key = f"overfit_ratio_{key}"
        if mean_key in overfit:
            parts = [f"mean_OOS_IC={overfit[mean_key]:+.4f}"]
            if overfit.get(ratio_key) is not None:
                parts.append(f"ratio={overfit[ratio_key]:.2f}")
            if overfit.get(f"ic_degradation_{key}") is not None:
                parts.append(f"degradation={overfit[f'ic_degradation_{key}']:+.4f}")
            print(f"  {key}: {', '.join(parts)}")

    verdict = overfit.get("verdict", "N/A")
    marker = {"PASS": "PASS", "WARNING": "WARNING !", "OVERFIT": "OVERFIT !!"}
    print(f"\n  Verdict: {marker.get(verdict, verdict)}")
    print("=" * 70)


def generate_markdown_report(
    fold_results: List[FoldResult],
    overfit: Dict[str, Any],
    config_path: Optional[str],
    output_path: Path,
):
    """Generate a markdown report file."""
    lines = [
        "# NACS Cross-Validation Report",
        "",
        f"Config: `{config_path or 'default v8'}`",
        f"Folds: {len(fold_results)}",
        "",
        "## Fold Results",
        "",
        "| Fold | Train Range | Train N | Test Range | Test N | OOS 30d IC | OOS 60d IC | OOS 180d IC |",
        "|------|-------------|---------|------------|--------|------------|------------|-------------|",
    ]

    for fr in fold_results:
        def _fmt_ic(key):
            if key in fr.test_ic and fr.test_ic[key]["ic"] is not None:
                return f"{fr.test_ic[key]['ic']:+.4f}"
            return "N/A"

        lines.append(
            f"| {fr.fold_id} | {fr.train_range[0]}~{fr.train_range[1]} | "
            f"{fr.train_n} | {fr.test_range[0]}~{fr.test_range[1]} | "
            f"{fr.test_n} | {_fmt_ic('30d')} | {_fmt_ic('60d')} | {_fmt_ic('180d')} |"
        )

    lines.extend([
        "",
        "## Overfit Diagnostics",
        "",
    ])

    for key in ("30d", "60d", "180d"):
        mean_k = f"ic_mean_oos_{key}"
        if mean_k in overfit:
            ratio = overfit.get(f"overfit_ratio_{key}")
            deg = overfit.get(f"ic_degradation_{key}")
            lines.append(f"- **{key}**: mean_OOS_IC={overfit[mean_k]:+.4f}"
                         + (f", ratio={ratio:.2f}" if ratio is not None else "")
                         + (f", degradation={deg:+.4f}" if deg is not None else ""))

    verdict = overfit.get("verdict", "N/A")
    lines.extend(["", f"**Verdict: {verdict}**", ""])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nMarkdown report: {output_path}")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="NACS 时序交叉验证回测")
    parser.add_argument("--db", default=str(ROOT / "data" / "nacs_real.db"),
                        help="SQLite DB 路径")
    parser.add_argument("--config", default=None,
                        help="NacsConfig YAML/JSON 路径")
    parser.add_argument("--workers", type=int, default=1,
                        help="并行 worker 数")
    parser.add_argument("--use-static-env", action="store_true",
                        help="使用旧的硬编码 MarketEnvironment")
    parser.add_argument("--report-md", default=None,
                        help="生成 markdown 报告路径")
    parser.add_argument("--skip-train-ic", action="store_true",
                        help="跳过训练集 IC 计算 (省时间)")
    parser.add_argument("--folds", default=None,
                        help="自定义 fold 边界 (格式: ts:te:vs:ve,...)")
    parser.add_argument("--out-json", default=None,
                        help="JSON 输出路径 (默认: data/derived/backtest/latest/cv_results.json)")
    args = parser.parse_args()

    # Load config if specified
    if args.config:
        from config import load_config, set_config
        cfg = load_config(args.config)
        set_config(cfg)
        print(f"Config: {args.config} (version={cfg.version})")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        sys.exit(1)

    # Build folds
    if args.folds:
        boundaries = parse_custom_folds(args.folds)
        folds = build_fold_specs(boundaries)
    else:
        folds = build_fold_specs()
    print(f"DB: {db_path}")
    print(f"Folds: {len(folds)}")

    # Load all listed IPOs
    conn = db_connect(str(db_path))
    all_ipos = conn.execute("""
        SELECT m.ipo_id, m.listing_date, m.pricing_date, r.return_d30
        FROM ipo_master m LEFT JOIN ipo_returns r ON m.ipo_id = r.ipo_id
        WHERE m.status = 'listed'
        ORDER BY m.listing_date
    """).fetchall()

    full_history = [
        (date.fromisoformat(str(x["listing_date"])[:10]) if x["listing_date"] else None,
         x["return_d30"])
        for x in all_ipos
    ]

    all_ipo_rows = [
        {
            "ipo_id": x["ipo_id"],
            "listing_date": (
                date.fromisoformat(str(x["listing_date"])[:10])
                if x["listing_date"] else None
            ),
            "pricing_date": x["pricing_date"],
        }
        for x in all_ipos
    ]
    conn.close()

    print(f"Total listed IPOs: {len(all_ipo_rows)}")
    print("=" * 60)

    # Run full-sample IC (for overfit ratio baseline)
    print("\nRunning full-sample scoring...")
    full_sample_ic = run_full_sample_ic(
        all_ipo_rows, full_history, str(db_path),
        workers=args.workers,
        use_static_env=args.use_static_env,
        config_path=args.config,
    )
    print(f"Full-sample IC: "
          + ", ".join(f"{k}={v['ic']:+.4f}" for k, v in full_sample_ic.items()
                      if v.get("ic") is not None))

    # Run each fold
    fold_results = []
    for fold in folds:
        print(f"\nFold {fold.fold_id}: "
              f"train=[{fold.train_start}, {fold.train_end}], "
              f"test=[{fold.test_start}, {fold.test_end}]")

        fr = run_one_fold(
            fold=fold,
            full_history=full_history,
            all_ipo_rows=all_ipo_rows,
            db_path=str(db_path),
            workers=args.workers,
            use_static_env=args.use_static_env,
            config_path=args.config,
            skip_train_ic=args.skip_train_ic,
        )
        fold_results.append(fr)

        # Quick inline output
        if fr.test_ic:
            for key in ("30d", "60d"):
                if key in fr.test_ic and fr.test_ic[key]["ic"] is not None:
                    print(f"  OOS {key} ic={fr.test_ic[key]['ic']:+.4f} "
                          f"(n={fr.test_ic[key]['n']})")

    # Compute overfit diagnostics
    overfit = compute_overfit_metrics(fold_results, full_sample_ic)

    # Print report
    print_fold_report(fold_results, overfit)

    # Save JSON
    json_path = Path(args.out_json) if args.out_json else (
        ROOT / "data" / "derived" / "backtest" / "latest" / "cv_results.json"
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "timestamp": date.today().isoformat(),
        "config_path": args.config,
        "n_folds": len(fold_results),
        "folds": [
            {
                "fold_id": fr.fold_id,
                "train_range": list(fr.train_range),
                "test_range": list(fr.test_range),
                "train_n": fr.train_n,
                "test_n": fr.test_n,
                "test_ic": fr.test_ic,
                "train_ic": fr.train_ic,
            }
            for fr in fold_results
        ],
        "full_sample_ic": full_sample_ic,
        "aggregate": {
            "ic_mean_oos": {
                key: overfit.get(f"ic_mean_oos_{key}")
                for key in ("30d", "60d", "180d")
                if f"ic_mean_oos_{key}" in overfit
            },
            "ic_std_oos": {
                key: overfit.get(f"ic_std_oos_{key}")
                for key in ("30d", "60d", "180d")
                if f"ic_std_oos_{key}" in overfit
            },
        },
        "overfit_diagnostics": overfit,
    }

    json_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nJSON output: {json_path}")

    # Optional markdown report
    if args.report_md:
        generate_markdown_report(
            fold_results, overfit, args.config, Path(args.report_md)
        )


if __name__ == "__main__":
    main()
