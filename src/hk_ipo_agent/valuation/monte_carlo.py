"""Monte Carlo engine per PROJECT_SPEC.md §3.7.

10000 sampled paths per assumption set. Models declare their assumption
distributions; this engine materializes the samples and computes the
model output per path.

Distributions supported:
- Normal(mean, std)
- LogNormal(mean, std)  — for non-negative quantities (revenue, multiples)
- Uniform(low, high)
- Triangular(low, mode, high)
- Bernoulli(p) — for milestone probability flags
- Constant(value) — for fixed-input assumptions

Reproducibility: a 64-bit ``seed`` parameter is accepted on every call.
Default is ``None`` (truly random); tests should pin a seed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

DEFAULT_PATHS: int = 10_000


# ---------------------------------------------------------------------------
# Distribution primitives
# ---------------------------------------------------------------------------


class Distribution(ABC):
    """A sampleable assumption distribution."""

    @abstractmethod
    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray: ...


@dataclass
class Constant(Distribution):
    value: float

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return np.full(n, self.value, dtype=np.float64)


@dataclass
class Normal(Distribution):
    mean: float
    std: float

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.normal(self.mean, self.std, size=n)


@dataclass
class LogNormal(Distribution):
    """LogNormal parameterized in terms of the underlying *normal* mu/sigma.

    For a desired median ``m`` and CV (coefficient of variation) ``cv``:
        sigma = sqrt(log(1 + cv**2))
        mu    = log(m)
    """

    mu: float
    sigma: float

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.lognormal(self.mu, self.sigma, size=n)


@dataclass
class Uniform(Distribution):
    low: float
    high: float

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.uniform(self.low, self.high, size=n)


@dataclass
class Triangular(Distribution):
    low: float
    mode: float
    high: float

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.triangular(self.low, self.mode, self.high, size=n)


@dataclass
class Bernoulli(Distribution):
    p: float

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return (rng.uniform(0.0, 1.0, size=n) < self.p).astype(np.float64)


@dataclass
class FromArray(Distribution):
    """Empirical distribution: sample with replacement from observed values."""

    values: np.ndarray

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        if self.values.size == 0:
            return np.full(n, np.nan, dtype=np.float64)
        idx = rng.integers(0, self.values.size, size=n)
        return self.values[idx]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


SampleSet = dict[str, np.ndarray]


def sample_assumptions(
    assumptions: dict[str, Distribution],
    *,
    n: int = DEFAULT_PATHS,
    seed: int | None = None,
) -> SampleSet:
    """Sample each named assumption ``n`` times. Returns ``{name: ndarray}``."""
    rng = np.random.default_rng(seed)
    return {name: dist.sample(n, rng) for name, dist in assumptions.items()}


def run_mc(
    assumptions: dict[str, Distribution],
    payoff: Callable[[SampleSet], np.ndarray],
    *,
    paths: int = DEFAULT_PATHS,
    seed: int | None = None,
) -> np.ndarray:
    """Run ``paths`` MC trials and return the per-path payoff array.

    Args:
        assumptions: ``{assumption_name: Distribution}``. Sampled into
                     same-shape arrays of length ``paths``.
        payoff:      pure function ``(sample_set) -> ndarray of length paths``
                     that computes the model output per path.
        paths:       number of MC trials (default ``DEFAULT_PATHS`` = 10k).
        seed:        RNG seed (None = system entropy; tests should pin).

    Returns:
        1-D ndarray of length ``paths``. Non-finite values pass through; the
        ``distribution_from_samples`` helper filters them out at percentile
        time.
    """
    samples = sample_assumptions(assumptions, n=paths, seed=seed)
    result = payoff(samples)
    if not isinstance(result, np.ndarray):
        result = np.asarray(result, dtype=np.float64)
    if result.shape != (paths,):
        raise ValueError(
            f"payoff returned shape {result.shape}; expected ({paths},)"
        )
    return result.astype(np.float64, copy=False)


__all__ = (
    "DEFAULT_PATHS",
    "Bernoulli",
    "Constant",
    "Distribution",
    "FromArray",
    "LogNormal",
    "Normal",
    "SampleSet",
    "Triangular",
    "Uniform",
    "run_mc",
    "sample_assumptions",
)


_ = Any  # type re-export marker (unused at module level)
