"""Latency and cost per question and per cell (Step 5).

Latency is reported per stage.  A cached query embedding takes about 0 ms, so
the embed stage counts only embeddings made in this run.  A replayed answer
reports the latency its API call had when it was made, which the response cache
stores.  Cost comes from the token counts each API reported and the dated price
table in the header, so a replayed answer still shows what it cost to produce.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import fmean

from retrieve.arm import ArmResult

PERCENTILES = (("p50", 0.50), ("p95", 0.95))
DEFINITIONS = {
    "latency_ms": (
        "p50 and p95 per stage, by linear interpolation between the closest ranks; embed and "
        "retrieval (embed plus search) count only questions whose query embedding was made in "
        "this run; generation is each answer call's API latency when it was made, also for an "
        "answer replayed from the cache"
    ),
    "cost_usd": (
        "from the token counts each API reported and the header's dated price table; Voyage at "
        "list price, before any free allowance"
    ),
}


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


def _stats(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0} | {name: None for name, _ in PERCENTILES}
    return {"n": len(values)} | {name: percentile(values, q) for name, q in PERCENTILES}


def latency_cell(results: Sequence[ArmResult]) -> dict[str, dict[str, float | int | None]]:
    """p50 and p95 latency per stage over *results*."""
    fresh = [r for r in results if not r.query_cached]
    return {
        "retrieval": _stats([r.retrieval_ms for r in fresh]),
        "embed": _stats([r.embed_ms for r in fresh]),
        "search": _stats([r.search_ms for r in results]),
        "generation": _stats([r.answer.generation_ms for r in results]),
    }


def question_cost(result: ArmResult) -> dict[str, float]:
    """USD for one question, per stage and in total."""
    return {
        "embedding": result.embed_cost_usd,
        "generation": result.answer.cost_usd,
        "total": result.embed_cost_usd + result.answer.cost_usd,
    }


def cost_cell(results: Sequence[ArmResult]) -> dict[str, float]:
    """Mean USD per question per stage, and the cell's total."""
    costs = [question_cost(r) for r in results]
    return {
        "per_query_embedding": fmean(c["embedding"] for c in costs),
        "per_query_generation": fmean(c["generation"] for c in costs),
        "per_query_total": fmean(c["total"] for c in costs),
        "total": sum(c["total"] for c in costs),
    }
