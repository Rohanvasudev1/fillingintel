"""Latency and cost per question and per cell (Step 5).

Latency is reported per stage.  A cached query embedding takes about 0 ms, so
the embed stage counts only embeddings made in this run.  A replayed answer
reports the latency its API call had when it was made, which the response cache
stores.  Cost comes from the token counts each API reported and the dated price
table in the header, so a replayed answer still shows what it cost to produce.
"""
from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from statistics import fmean

from eval.bootstrap import RngFor, bootstrap_interval, mean_interval, percentile
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


def _stats(values: Sequence[float], stage: str, rng_for: RngFor) -> dict[str, object]:
    if not values:
        return ({"n": 0} | {name: None for name, _ in PERCENTILES}
                | {"intervals": {name: None for name, _ in PERCENTILES}})
    return (
        {"n": len(values)}
        | {name: percentile(values, q) for name, q in PERCENTILES}
        | {"intervals": {
            name: bootstrap_interval(values, partial(percentile, q=q),
                                     rng_for(f"latency_ms.{stage}.{name}"))
            for name, q in PERCENTILES
        }}
    )


def latency_cell(results: Sequence[ArmResult], rng_for: RngFor) -> dict[str, dict[str, object]]:
    """p50 and p95 latency per stage over *results*, each with a bootstrap interval."""
    fresh = [r for r in results if not r.query_cached]
    return {
        "retrieval": _stats([r.retrieval_ms for r in fresh], "retrieval", rng_for),
        "embed": _stats([r.embed_ms for r in fresh], "embed", rng_for),
        "search": _stats([r.search_ms for r in results], "search", rng_for),
        "generation": _stats([r.answer.generation_ms for r in results], "generation", rng_for),
    }


def question_cost(result: ArmResult) -> dict[str, float]:
    """USD for one question, per stage and in total."""
    return {
        "embedding": result.embed_cost_usd,
        "generation": result.answer.cost_usd,
        "total": result.embed_cost_usd + result.answer.cost_usd,
    }


_PER_QUERY = {"per_query_embedding": "embedding", "per_query_generation": "generation",
              "per_query_total": "total"}


def cost_cell(results: Sequence[ArmResult], rng_for: RngFor) -> dict[str, object]:
    """Mean USD per question per stage with bootstrap intervals, and the cell's total."""
    costs = [question_cost(r) for r in results]
    return {
        **{name: fmean(c[stage] for c in costs) for name, stage in _PER_QUERY.items()},
        "total": sum(c["total"] for c in costs),
        "intervals": {
            name: mean_interval([c[stage] for c in costs], rng_for(f"cost_usd.{name}"))
            for name, stage in _PER_QUERY.items()
        },
    }
