"""Bootstrap 95% intervals for the results cells (Step 5, ticket 06).

Percentile bootstrap: resample the cell's questions with replacement
``BOOTSTRAP_RESAMPLES`` times, compute the statistic on each resample, and take
the 2.5th and 97.5th percentiles.  Each interval draws from its own generator,
seeded by the run's seed and the interval's key (set, arm, class, metric), so
an interval does not change when another cell or metric is added.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from statistics import fmean
from typing import TypeVar

BOOTSTRAP_RESAMPLES = 10_000
CONFIDENCE = 0.95
DIRECTIONAL_BELOW = 10  # research plan: cells with fewer scored questions are "directional"
DEFINITIONS = {
    "interval": (
        f"percentile bootstrap {CONFIDENCE:.0%} interval over {BOOTSTRAP_RESAMPLES} resamples "
        "of the cell's questions, with replacement; each interval's generator is seeded by "
        "the header's seed and the interval's set, arm, class and metric; for a ratio, "
        "resamples with a zero denominator are left out and 'resamples' counts the rest"
    ),
    "directional": (
        f"the cell has fewer than {DIRECTIONAL_BELOW} questions in the scored split "
        "(research plan)"
    ),
}

T = TypeVar("T")
Interval = dict[str, float | int]
RngFor = Callable[[str], random.Random]  # metric name -> that interval's generator


def percentile(values: Sequence[float], q: float) -> float:
    """The *q* quantile (0 to 1) of *values*, interpolating linearly between closest ranks."""
    if not values:
        raise ValueError("no values")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be between 0 and 1, got {q}")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def is_directional(n: int) -> bool:
    """Whether a cell of *n* scored questions is too small to count as more than directional."""
    return n < DIRECTIONAL_BELOW


def interval_rng(seed: int, *key: str) -> random.Random:
    """A generator for one interval; string seeds hash the same in every process."""
    return random.Random("|".join([str(seed), *key]))


def bootstrap_interval(
    rows: Sequence[T], statistic: Callable[[Sequence[T]], float | None], rng: random.Random
) -> Interval | None:
    """The interval of *statistic* over resamples of *rows*; None if it is never defined."""
    if not rows:
        return None
    draws = (statistic(rng.choices(rows, k=len(rows))) for _ in range(BOOTSTRAP_RESAMPLES))
    values = [v for v in draws if v is not None]
    if not values:
        return None
    tail = (1 - CONFIDENCE) / 2
    return {
        "low": percentile(values, tail),
        "high": percentile(values, 1 - tail),
        "resamples": len(values),
    }


def mean_interval(values: Sequence[float], rng: random.Random) -> Interval | None:
    """The interval of the mean of *values*."""
    return bootstrap_interval(values, fmean, rng)


def _ratio(pairs: Sequence[tuple[float, float]]) -> float | None:
    denominator = sum(d for _, d in pairs)
    return sum(n for n, _ in pairs) / denominator if denominator else None


def ratio_interval(pairs: Sequence[tuple[float, float]], rng: random.Random) -> Interval | None:
    """The interval of sum(numerators) / sum(denominators) over (numerator, denominator) pairs."""
    return bootstrap_interval(pairs, _ratio, rng)
