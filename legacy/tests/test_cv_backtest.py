"""
Tests for run_cv_backtest.py — NACS 时序交叉验证框架

T1: build_fold_specs 生成正确的 anchored expanding window
T2: filter_history_for_fold 严格过滤未来数据 (核心防泄露测试)
T3: compute_fold_ic 合成数据 IC 计算
T4: compute_overfit_metrics 边界条件
T5: verdict 分类逻辑 (PASS/WARNING/OVERFIT)
T6: custom fold 边界解析
T7: 空 fold graceful degradation
"""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

import pytest

# Ensure src/ and project root on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from run_cv_backtest import (
    FoldSpec,
    FoldResult,
    build_fold_specs,
    filter_history_for_fold,
    compute_fold_ic,
    compute_overfit_metrics,
    parse_custom_folds,
    _compute_verdict,
    DEFAULT_BOUNDARIES,
)


# =============================================================================
# T1: build_fold_specs
# =============================================================================

class TestBuildFoldSpecs:
    def test_default_produces_6_folds(self):
        folds = build_fold_specs()
        assert len(folds) == 6

    def test_all_folds_anchored_to_2022(self):
        folds = build_fold_specs()
        for f in folds:
            assert f.train_start == date(2022, 1, 1), \
                f"Fold {f.fold_id} train_start should be 2022-01-01"

    def test_train_window_expanding(self):
        folds = build_fold_specs()
        train_ends = [f.train_end for f in folds]
        for i in range(1, len(train_ends)):
            assert train_ends[i] > train_ends[i - 1], \
                f"Fold {i} train_end should be after fold {i-1}"

    def test_test_follows_train(self):
        folds = build_fold_specs()
        for f in folds:
            assert f.test_start > f.train_end, \
                f"Fold {f.fold_id}: test_start should be after train_end"

    def test_custom_boundaries(self):
        custom = [
            (date(2022, 1, 1), date(2023, 12, 31),
             date(2024, 1, 1), date(2024, 12, 31)),
        ]
        folds = build_fold_specs(custom)
        assert len(folds) == 1
        assert folds[0].fold_id == 0
        assert folds[0].train_end == date(2023, 12, 31)
        assert folds[0].test_start == date(2024, 1, 1)

    def test_fold_ids_sequential(self):
        folds = build_fold_specs()
        for i, f in enumerate(folds):
            assert f.fold_id == i


# =============================================================================
# T2: filter_history_for_fold (core anti-leakage)
# =============================================================================

class TestFilterHistoryForFold:
    @pytest.fixture
    def sample_history(self):
        return [
            (date(2022, 3, 1), 0.05),
            (date(2022, 9, 1), -0.02),
            (date(2023, 2, 1), 0.10),
            (date(2023, 8, 1), 0.08),
            (date(2024, 1, 15), -0.05),
            (date(2024, 7, 1), 0.12),
            (date(2025, 3, 1), 0.03),
            (None, 0.07),  # missing date
        ]

    def test_filters_future_data(self, sample_history):
        filtered = filter_history_for_fold(sample_history, date(2023, 6, 30))
        dates = [d for d, _ in filtered]
        assert all(d <= date(2023, 6, 30) for d in dates)
        assert len(filtered) == 3  # 2022-03, 2022-09, 2023-02

    def test_excludes_none_dates(self, sample_history):
        filtered = filter_history_for_fold(sample_history, date(2030, 1, 1))
        assert all(d is not None for d, _ in filtered)
        # All non-None dates should be included (7 of 8)
        assert len(filtered) == 7

    def test_boundary_date_inclusive(self, sample_history):
        # train_end exactly equals a data point date
        filtered = filter_history_for_fold(sample_history, date(2023, 8, 1))
        dates = [d for d, _ in filtered]
        assert date(2023, 8, 1) in dates
        assert len(filtered) == 4

    def test_empty_if_all_future(self, sample_history):
        filtered = filter_history_for_fold(sample_history, date(2021, 1, 1))
        assert len(filtered) == 0

    def test_preserves_return_values(self, sample_history):
        filtered = filter_history_for_fold(sample_history, date(2022, 9, 1))
        assert len(filtered) == 2
        returns = [r for _, r in filtered]
        assert 0.05 in returns
        assert -0.02 in returns

    def test_empty_history(self):
        filtered = filter_history_for_fold([], date(2023, 6, 30))
        assert len(filtered) == 0


# =============================================================================
# T3: compute_fold_ic
# =============================================================================

class TestComputeFoldIC:
    def test_basic_positive_ic(self):
        # NACS and returns perfectly rank-correlated
        records = [
            {"NACS": i * 0.1, "r5d": i * 0.01, "r30d": i * 0.02,
             "r60d": i * 0.03, "r180d": i * 0.04}
            for i in range(20)
        ]
        result = compute_fold_ic(records)
        assert "30d" in result
        assert result["30d"]["ic"] is not None
        assert result["30d"]["ic"] > 0.9  # near-perfect correlation
        assert result["30d"]["n"] == 20

    def test_empty_records(self):
        result = compute_fold_ic([])
        assert result == {}

    def test_insufficient_data(self):
        records = [
            {"NACS": 0.5, "r5d": 0.01, "r30d": 0.02, "r60d": 0.03, "r180d": 0.04}
            for _ in range(3)
        ]
        result = compute_fold_ic(records)
        # ic() returns nan when n < 5
        for key in ("30d", "60d"):
            if key in result:
                assert result[key]["n"] < 5

    def test_handles_nan_returns(self):
        import numpy as np
        records = [
            {"NACS": i * 0.1, "r5d": i * 0.01, "r30d": i * 0.02,
             "r60d": float("nan"), "r180d": None}
            for i in range(20)
        ]
        result = compute_fold_ic(records)
        assert result["30d"]["ic"] is not None
        assert result["30d"]["n"] == 20
        # 60d/180d should have fewer valid pairs
        if "60d" in result:
            assert result["60d"]["n"] == 0 or result["60d"]["ic"] is None


# =============================================================================
# T4: compute_overfit_metrics
# =============================================================================

class TestComputeOverfitMetrics:
    def _make_fold_result(self, fold_id, oos_60d_ic, train_60d_ic=None):
        test_ic = {"60d": {"ic": oos_60d_ic, "n": 30, "ls_spread": 0.05, "ls_t_stat": 1.5}}
        train_ic = {}
        if train_60d_ic is not None:
            train_ic = {"60d": {"ic": train_60d_ic, "n": 80, "ls_spread": 0.08, "ls_t_stat": 2.0}}
        return FoldResult(
            fold_id=fold_id,
            train_range=("2022-01-01", "2023-06-30"),
            test_range=("2023-07-01", "2023-12-31"),
            train_n=80, test_n=30,
            test_ic=test_ic, train_ic=train_ic,
        )

    def test_all_positive_oos(self):
        frs = [self._make_fold_result(i, 0.10 + i * 0.01) for i in range(6)]
        metrics = compute_overfit_metrics(frs)
        assert metrics["ic_mean_oos_60d"] > 0
        assert metrics["n_folds_negative_60d"] == 0

    def test_overfit_ratio_with_full_sample(self):
        frs = [self._make_fold_result(i, 0.10, 0.20) for i in range(4)]
        full_ic = {"60d": {"ic": 0.15}}
        metrics = compute_overfit_metrics(frs, full_ic)
        assert metrics["overfit_ratio_60d"] == pytest.approx(1.5, abs=0.01)

    def test_ic_degradation(self):
        frs = [self._make_fold_result(i, 0.10, 0.20) for i in range(4)]
        metrics = compute_overfit_metrics(frs)
        assert metrics["ic_degradation_60d"] == pytest.approx(0.10, abs=0.01)

    def test_no_full_sample_ic(self):
        frs = [self._make_fold_result(i, 0.10) for i in range(4)]
        metrics = compute_overfit_metrics(frs)
        assert metrics["overfit_ratio_60d"] is None

    def test_empty_fold_results(self):
        metrics = compute_overfit_metrics([])
        assert "verdict" in metrics


# =============================================================================
# T5: verdict logic
# =============================================================================

class TestVerdictLogic:
    def test_pass_verdict(self):
        frs = [
            FoldResult(i, ("", ""), ("", ""), 80, 30,
                       {"60d": {"ic": 0.12, "n": 30, "ls_spread": 0.05, "ls_t_stat": 1.5}},
                       {"60d": {"ic": 0.14, "n": 80, "ls_spread": 0.05, "ls_t_stat": 2.0}})
            for i in range(6)
        ]
        full_ic = {"60d": {"ic": 0.13}}
        metrics = compute_overfit_metrics(frs, full_ic)
        assert metrics["verdict"] == "PASS"

    def test_overfit_ratio_triggers_overfit(self):
        # OOS IC = 0.05, full sample IC = 0.15 => ratio = 3.0
        frs = [
            FoldResult(i, ("", ""), ("", ""), 80, 30,
                       {"60d": {"ic": 0.05, "n": 30, "ls_spread": 0.02, "ls_t_stat": 1.0}},
                       {})
            for i in range(6)
        ]
        full_ic = {"60d": {"ic": 0.15}}
        metrics = compute_overfit_metrics(frs, full_ic)
        assert metrics["verdict"] == "OVERFIT"

    def test_majority_negative_triggers_overfit(self):
        # 4 out of 6 folds negative
        oos_ics = [-0.05, -0.03, 0.02, -0.04, -0.01, 0.01]
        frs = [
            FoldResult(i, ("", ""), ("", ""), 80, 30,
                       {"60d": {"ic": oos_ics[i], "n": 30, "ls_spread": 0.01, "ls_t_stat": 0.5}},
                       {})
            for i in range(6)
        ]
        metrics = compute_overfit_metrics(frs)
        assert metrics["verdict"] == "OVERFIT"

    def test_warning_on_moderate_ratio(self):
        # OOS IC = 0.08, full sample = 0.14 => ratio = 1.75
        frs = [
            FoldResult(i, ("", ""), ("", ""), 80, 30,
                       {"60d": {"ic": 0.08, "n": 30, "ls_spread": 0.03, "ls_t_stat": 1.2}},
                       {})
            for i in range(6)
        ]
        full_ic = {"60d": {"ic": 0.14}}
        metrics = compute_overfit_metrics(frs, full_ic)
        assert metrics["verdict"] == "WARNING"

    def test_warning_on_high_degradation(self):
        # train IC = 0.25, OOS IC = 0.10 => degradation = 0.15 > 0.10
        frs = [
            FoldResult(i, ("", ""), ("", ""), 80, 30,
                       {"60d": {"ic": 0.10, "n": 30, "ls_spread": 0.03, "ls_t_stat": 1.2}},
                       {"60d": {"ic": 0.25, "n": 80, "ls_spread": 0.06, "ls_t_stat": 2.5}})
            for i in range(6)
        ]
        metrics = compute_overfit_metrics(frs)
        assert metrics["verdict"] == "WARNING"


# =============================================================================
# T6: custom fold boundary parsing
# =============================================================================

class TestCustomFoldParsing:
    def test_single_fold(self):
        spec = "2022-01-01:2023-06-30:2023-07-01:2023-12-31"
        boundaries = parse_custom_folds(spec)
        assert len(boundaries) == 1
        assert boundaries[0][0] == date(2022, 1, 1)
        assert boundaries[0][3] == date(2023, 12, 31)

    def test_multiple_folds(self):
        spec = ("2022-01-01:2023-06-30:2023-07-01:2023-12-31,"
                "2022-01-01:2023-12-31:2024-01-01:2024-06-30")
        boundaries = parse_custom_folds(spec)
        assert len(boundaries) == 2

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="4 dates"):
            parse_custom_folds("2022-01-01:2023-06-30:2023-07-01")

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            parse_custom_folds("2022-13-01:2023-06-30:2023-07-01:2023-12-31")


# =============================================================================
# T7: empty fold graceful degradation
# =============================================================================

class TestEmptyFoldDegradation:
    def test_compute_fold_ic_empty(self):
        result = compute_fold_ic([])
        assert result == {}

    def test_overfit_metrics_single_fold(self):
        fr = FoldResult(
            0, ("2022-01-01", "2023-06-30"), ("2023-07-01", "2023-12-31"),
            80, 0, {}, {},
        )
        metrics = compute_overfit_metrics([fr])
        assert "verdict" in metrics

    def test_overfit_metrics_all_none_ic(self):
        frs = [
            FoldResult(i, ("", ""), ("", ""), 80, 5,
                       {"60d": {"ic": None, "n": 3, "ls_spread": None, "ls_t_stat": None}},
                       {})
            for i in range(3)
        ]
        metrics = compute_overfit_metrics(frs)
        assert "verdict" in metrics
        # No valid IC data => should still produce a verdict
        assert metrics["verdict"] in ("PASS", "WARNING", "OVERFIT")
