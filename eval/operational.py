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
from retrieve.arm import GRAPH_STAGES, ArmResult, GraphStage

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
GRAPH_DEFINITIONS = {  # in a graph arm's header only
    "graph_stages": (
        "anchor_linking, cypher_generation, cypher_repair and cypher_execution latency counts "
        "the questions that ran the stage; their cost is a mean over every question, a stage "
        "that did not run costing 0; search covers every stage after the query embedding"
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


GLOBAL_DEFINITIONS = {  # in graph_global's header only
    "graph_stages": (
        "community_select and global_map latency counts the questions that ran the stage; "
        "global_map's is the map step's wall time, its tokens and cost summed over the map "
        "calls; their cost is a mean over every question, a stage that did not run costing 0; "
        "search covers every stage after the query embedding; the reduce call writes the "
        "answer, so its latency and cost are generation's"
    ),
}


def _stages(results: Sequence[ArmResult]) -> tuple[GraphStage, ...]:
    """The graph stages *results*' arm families report, in ``GRAPH_STAGES`` order."""
    names = {s for r in results if r.graph is not None for s in r.graph.stage_names}
    return tuple(s for s in GRAPH_STAGES if s in names)


def latency_cell(results: Sequence[ArmResult], rng_for: RngFor) -> dict[str, dict[str, object]]:
    """p50 and p95 latency per stage over *results*, each with a bootstrap interval.

    A graph arm's cell adds each graph stage, over the questions that ran it.
    """
    fresh = [r for r in results if not r.query_cached]
    graphs = [r.graph for r in results if r.graph is not None]
    return {
        "retrieval": _stats([r.retrieval_ms for r in fresh], "retrieval", rng_for),
        "embed": _stats([r.embed_ms for r in fresh], "embed", rng_for),
        "search": _stats([r.search_ms for r in results], "search", rng_for),
        "generation": _stats([r.answer.generation_ms for r in results], "generation", rng_for),
        **{stage: _stats([g.stages[stage].ms for g in graphs if stage in g.stages], stage,
                         rng_for)
           for stage in _stages(results)},
    }


def question_cost(result: ArmResult) -> dict[str, float]:
    """USD for one question, per stage and in total; a graph stage that did not run costs 0."""
    stages = {}
    if result.graph is not None:
        stages = {stage: (result.graph.stages[stage].cost_usd
                          if stage in result.graph.stages else 0.0)
                  for stage in result.graph.stage_names}
    return {
        "embedding": result.embed_cost_usd,
        **stages,
        "generation": result.answer.cost_usd,
        "total": result.embed_cost_usd + sum(stages.values()) + result.answer.cost_usd,
    }


def _per_query(results: Sequence[ArmResult]) -> dict[str, str]:
    """Each cost cell field and the question-cost stage it averages."""
    stages = ("embedding", *_stages(results), "generation", "total")
    return {f"per_query_{stage}": stage for stage in stages}


def cost_cell(results: Sequence[ArmResult], rng_for: RngFor) -> dict[str, object]:
    """Mean USD per question per stage with bootstrap intervals, and the cell's total."""
    costs = [question_cost(r) for r in results]
    per_query = _per_query(results)
    return {
        **{name: fmean(c[stage] for c in costs) for name, stage in per_query.items()},
        "total": sum(c["total"] for c in costs),
        "intervals": {
            name: mean_interval([c[stage] for c in costs], rng_for(f"cost_usd.{name}"))
            for name, stage in per_query.items()
        },
    }
