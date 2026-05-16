"""Tests for ``hk_ipo_agent.valuation.monte_carlo`` engine + distributions."""

from __future__ import annotations

import numpy as np
import pytest

from hk_ipo_agent.valuation.monte_carlo import (
    DEFAULT_PATHS,
    Bernoulli,
    Constant,
    FromArray,
    LogNormal,
    Normal,
    Triangular,
    Uniform,
    run_mc,
    sample_assumptions,
)


def test_constant_returns_value() -> None:
    rng = np.random.default_rng(0)
    arr = Constant(value=3.14).sample(100, rng)
    assert arr.shape == (100,)
    assert (arr == 3.14).all()


def test_normal_seed_reproducible() -> None:
    a = Normal(0.0, 1.0).sample(1000, np.random.default_rng(42))
    b = Normal(0.0, 1.0).sample(1000, np.random.default_rng(42))
    np.testing.assert_array_equal(a, b)


def test_lognormal_strictly_positive() -> None:
    arr = LogNormal(mu=0.0, sigma=1.0).sample(5000, np.random.default_rng(0))
    assert (arr > 0).all()


def test_uniform_bounded() -> None:
    arr = Uniform(low=2.0, high=5.0).sample(5000, np.random.default_rng(0))
    assert arr.min() >= 2.0
    assert arr.max() <= 5.0


def test_triangular_mode_dominates() -> None:
    arr = Triangular(low=0.0, mode=10.0, high=10.0).sample(
        20_000, np.random.default_rng(0)
    )
    # Mode at upper bound → mean should be skewed high.
    assert arr.mean() > 6.0


def test_bernoulli_probability() -> None:
    arr = Bernoulli(p=0.3).sample(50_000, np.random.default_rng(0))
    assert 0.28 < arr.mean() < 0.32


def test_from_array_sampling() -> None:
    src = np.array([1.0, 2.0, 3.0])
    arr = FromArray(values=src).sample(10_000, np.random.default_rng(0))
    assert set(arr.tolist()) == {1.0, 2.0, 3.0}


def test_from_array_empty_returns_nan() -> None:
    arr = FromArray(values=np.array([])).sample(100, np.random.default_rng(0))
    assert np.isnan(arr).all()


def test_sample_assumptions_returns_named_dict() -> None:
    samples = sample_assumptions(
        {"x": Constant(1.0), "y": Normal(0, 1)},
        n=500,
        seed=7,
    )
    assert set(samples.keys()) == {"x", "y"}
    assert samples["x"].shape == (500,)
    assert samples["y"].shape == (500,)


def test_run_mc_default_paths() -> None:
    out = run_mc(
        {"a": Constant(2.0)},
        payoff=lambda s: s["a"] * 3.0,
        seed=0,
    )
    assert out.shape == (DEFAULT_PATHS,)
    assert (out == 6.0).all()


def test_run_mc_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="payoff returned shape"):
        run_mc(
            {"a": Constant(1.0)},
            payoff=lambda s: np.zeros(5),  # wrong shape
            paths=100,
            seed=0,
        )


def test_run_mc_reproducible_with_seed() -> None:
    a = run_mc({"x": Normal(0, 1)}, payoff=lambda s: s["x"] * 2, seed=123)
    b = run_mc({"x": Normal(0, 1)}, payoff=lambda s: s["x"] * 2, seed=123)
    np.testing.assert_array_equal(a, b)
