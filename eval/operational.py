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
from retrieve.arm import GRAPH_STAGES, ArmResult, GraphStage, Retrieval

ROUTING = "routing"  # the router's classifier call (Step 13)

PERCENTILES = (("p50", 0.50), ("p95", 0.95))
DEFINITIONS = {
    "latency_ms": (
        "p50 and p95 per stage, by linear interpolation between the closest ranks; embed and "
        "retrieval (embed plus search) count only questions whose query embedding was made in "
        "this run; generation is each answer call's API latency when it was made, also for an "
        "answer replayed from the cache"
    ),
    "total_ms": (
        "the wall time of each question's whole arm call, p50 and p95 by linear interpolation "
        "over every question in the cell; replayed counts the questions with any call replayed "
        "from a cache (query embedding, answer, Cypher or map call), whose total is shorter "
        "than a fresh run's, and each such question record says replayed"
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


ROUTER_DEFINITIONS = {  # in a router run's header only
    "routing": (
        "the router's classifier call: its latency is the call's API latency when it was made "
        "(also when replayed) and its cost is in every question's total; a question the router "
        "declined makes no retrieval or answer call and is left out of the retrieval, embed, "
        "search and generation latency"
    ),
}


GLOBAL_DEFINITIONS = {  # in graph_global's header only
    "graph_stages": (
        "community_select and global_map latency counts the questions that ran the stage; "
        "global_map's is the map step's wall time, its tokens and cost summed over the map "
        "calls; their cost is a mean over every question, a stage that did not run costing 0; "
        "search covers every stage after the query embedding; the reduce call writes the "
        "answer, so its latency and cost are generation's"
    ),
}


def _stages(results: Sequence[Retrieval]) -> tuple[GraphStage, ...]:
    """The graph stages *results*' arm families report, in ``GRAPH_STAGES`` order."""
    names = {s for r in results if r.graph is not None for s in r.graph.stage_names}
    return tuple(s for s in GRAPH_STAGES if s in names)


def latency_cell(results: Sequence[Retrieval], rng_for: RngFor) -> dict[str, dict[str, object]]:
    """p50 and p95 latency per stage over *results*, each with a bootstrap interval.

    Answered results add generation; retrieval-only ones have none.  A graph arm's
    cell adds each graph stage, over the questions that ran it.  A router cell adds
    routing over every question, and leaves the questions it declined out of the rest.
    """
    routed = [r.routing for r in results if r.routing is not None]
    results = [r for r in results if r.routing is None or not r.routing.declined]
    fresh = [r for r in results if not r.query_cached]
    graphs = [r.graph for r in results if r.graph is not None]
    answered = [r for r in results if isinstance(r, ArmResult)]
    return {
        "retrieval": _stats([r.retrieval_ms for r in fresh], "retrieval", rng_for),
        "embed": _stats([r.embed_ms for r in fresh], "embed", rng_for),
        "search": _stats([r.search_ms for r in results], "search", rng_for),
        **({"generation": _stats([r.answer.generation_ms for r in answered], "generation",
                                 rng_for)} if answered else {}),
        **{stage: _stats([g.stages[stage].ms for g in graphs if stage in g.stages], stage,
                         rng_for)
           for stage in _stages(results)},
        **({ROUTING: _stats([r.cost.ms for r in routed], ROUTING, rng_for)} if routed else {}),
    }


def replayed(result: ArmResult) -> bool:
    """Whether any of the question's calls replayed from a cache: its query embedding, its
    answer, a graph stage's call, a map call or the router's classifier call."""
    graph = result.graph
    return (result.query_cached or result.answer.from_cache
            or (result.routing is not None and result.routing.cost.from_cache)
            or (graph is not None and (
                any(stage.from_cache for stage in graph.stages.values())
                or (graph.global_search is not None
                    and any(c.cost.from_cache for c in graph.global_search.calls)))))


def total_cell(questions: Sequence[tuple[float, bool]], rng_for: RngFor) -> dict[str, object]:
    """p50 and p95 of each question's ``(total_ms, replayed)`` with bootstrap intervals, and
    how many questions replayed a call."""
    return {**_stats([ms for ms, _ in questions], "total", rng_for),
            "replayed": sum(flag for _, flag in questions)}


def question_cost(result: ArmResult) -> dict[str, float]:
    """USD for one question, per stage and in total; a graph stage that did not run costs 0.
    A router question adds routing."""
    stages = {}
    if result.graph is not None:
        stages = {stage: (result.graph.stages[stage].cost_usd
                          if stage in result.graph.stages else 0.0)
                  for stage in result.graph.stage_names}
    if result.routing is not None:
        stages[ROUTING] = result.routing.cost.cost_usd
    return {
        "embedding": result.embed_cost_usd,
        **stages,
        "generation": result.answer.cost_usd,
        "total": result.embed_cost_usd + sum(stages.values()) + result.answer.cost_usd,
    }


def _per_query(results: Sequence[ArmResult]) -> dict[str, str]:
    """Each cost cell field and the question-cost stage it averages."""
    routing = (ROUTING,) if any(r.routing is not None for r in results) else ()
    stages = ("embedding", *_stages(results), *routing, "generation", "total")
    return {f"per_query_{stage}": stage for stage in stages}


def cost_cell(results: Sequence[ArmResult], rng_for: RngFor) -> dict[str, object]:
    """Mean USD per question per stage with bootstrap intervals, and the cell's total; a
    stage a question did not have (another arm family's, under the router) costs it 0."""
    costs = [question_cost(r) for r in results]
    per_query = _per_query(results)
    return {
        **{name: fmean(c.get(stage, 0.0) for c in costs) for name, stage in per_query.items()},
        "total": sum(c["total"] for c in costs),
        "intervals": {
            name: mean_interval([c.get(stage, 0.0) for c in costs],
                                rng_for(f"cost_usd.{name}"))
            for name, stage in per_query.items()
        },
    }
