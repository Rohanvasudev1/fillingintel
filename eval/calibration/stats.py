"""Agreement statistics between the user's scores and the judge's (Step 13 ticket 04).

Per score type, as the RESEARCH-PLAN amendment of 2026-10-16 sets them:
Spearman's rho with a Fisher-z interval, mean absolute error and mean signed
difference for faithfulness and relevancy; Cohen's kappa for citation support,
with an interval bootstrapped by answer, since one answer's sentences are not
independent; percent agreement and the disagreement count for decline and
not-found.  Differences are judge minus human, so a positive bias means the
judge scores higher.  A statistic that is undefined returns None.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from statistics import fmean

from eval.bootstrap import Interval, bootstrap_interval

Z_95 = 1.959963984540054
MIN_PAIRS = 3


def _ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks, ties given the mean of the ranks they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for i in order[start:end + 1]:
            ranks[i] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    mx, my = fmean(xs), fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rho: Pearson's r on average ranks; None under 3 pairs or for a constant."""
    if len(xs) != len(ys):
        raise ValueError(f"{len(xs)} and {len(ys)} values")
    if len(xs) < MIN_PAIRS:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def fisher_interval(rho: float, n: int) -> tuple[float, float] | None:
    """The 95% interval of *rho* over *n* pairs by Fisher's z; None when n is 3 or less."""
    if n <= MIN_PAIRS:
        return None
    bounded = max(min(rho, 1 - 1e-12), -1 + 1e-12)
    z, half = math.atanh(bounded), Z_95 / math.sqrt(n - 3)
    return math.tanh(z - half), math.tanh(z + half)


def mean_absolute_error(human: Sequence[float], judge: Sequence[float]) -> float:
    return fmean(abs(j - h) for h, j in zip(human, judge, strict=True))


def mean_signed_difference(human: Sequence[float], judge: Sequence[float]) -> float:
    """Judge minus human, averaged."""
    return fmean(j - h for h, j in zip(human, judge, strict=True))


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Cohen's kappa for two raters' yes/no verdicts; None when chance agreement is total."""
    if len(a) != len(b):
        raise ValueError(f"{len(a)} and {len(b)} verdicts")
    if not a:
        return None
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    if chance == 1:
        return None
    return (observed - chance) / (1 - chance)


def percent_agreement(a: Sequence[bool], b: Sequence[bool]) -> tuple[float, int]:
    """The share of verdicts that agree, and how many do not."""
    if len(a) != len(b) or not a:
        raise ValueError(f"need equal, non-empty verdict lists, got {len(a)} and {len(b)}")
    disagreements = sum(x != y for x, y in zip(a, b, strict=True))
    return (len(a) - disagreements) / len(a), disagreements


AnswerVerdicts = tuple[Sequence[bool], Sequence[bool]]  # (human, judge) for one answer


def pooled_kappa(answers: Sequence[AnswerVerdicts]) -> float | None:
    """Kappa over every sentence of every answer."""
    return cohen_kappa([v for h, _ in answers for v in h], [v for _, j in answers for v in j])


def kappa_by_answer_interval(answers: Sequence[AnswerVerdicts],
                             rng: random.Random) -> Interval | None:
    """The percentile bootstrap interval of pooled kappa, resampling whole answers."""
    return bootstrap_interval(answers, pooled_kappa, rng)
