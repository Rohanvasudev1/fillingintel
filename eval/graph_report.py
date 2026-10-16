"""What a graph arm adds to the results file (Step 11).

A vector run has none of these fields, so its file is unchanged.  A graph run's
header records the graph it read, each question record holds its anchors, Cypher
attempts, map calls and stage costs, and each cell counts the questions that linked no
anchor, retrieved nothing or were answered "not found", so a good unanswerable
score can be read for what it is.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict

from retrieve.arm import GRAPH_STAGES, ArmResult, GlobalSearch, GraphProvenance, GraphRetrieval

DEFINITIONS = {
    "graph": (
        "graph arms only: no_anchor counts questions that linked no anchor, empty_retrieval "
        "those whose retrieval held no chunk, not_found those answered not_found"
    ),
    "cypher_validity": (
        "graph_traversal only, per question: first_pass_valid when the first query passed the "
        "guard and ran without error in the timeout; valid_after_repair when the first or its "
        "one repair did; non_empty when one returned chunks; fallback when the question fell "
        "back to graph_local; null with no query"
    ),
}

GLOBAL_DEFINITIONS = {  # in graph_global's header only
    "global_search": (
        "graph_global only: map points are dropped when they cite no chunk, cite a chunk their "
        "call was not shown, or have no score from 0 to 100; the reduce call reads the kept "
        "points above 0 by score, then community rank, within reduce_budget_tokens "
        "(cl100k_base); with none, the answer is not_found and no reduce call is made, "
        "counted as no_reduce; map_calls_unusable counts map replies cut off, refused or off "
        "the schema, which give their community no points; no_community counts questions with "
        "no community selected; the reduce call writes the answer, so its cost and latency "
        "are generation's"
    ),
}


def header_graph(provenance: GraphProvenance) -> dict[str, object]:
    """The header's record of the graph and Cypher prompt an arm read."""
    return {
        "ontology_version": provenance.ontology_version,
        "schema_sha256": provenance.schema_sha256,
        "load_run": provenance.load_run,
        "cypher_prompt": (
            {"version": provenance.cypher_prompt_version,
             "sha256": provenance.cypher_prompt_sha256}
            if provenance.cypher_prompt_version is not None else None
        ),
        **({"global_search": asdict(provenance.global_search)}
           if provenance.global_search is not None else {}),
    }


def question_graph(graph: GraphRetrieval) -> dict[str, object]:
    """One question's graph details, stages in ``GRAPH_STAGES`` order."""
    return {
        "anchors": [asdict(a) for a in graph.anchors],
        "hops": graph.hops,
        "no_anchor": graph.no_anchor,
        "cypher_attempts": [asdict(a) for a in graph.cypher_attempts],
        "fallback_reason": graph.fallback_reason,
        "validity": {
            "first_pass_valid": graph.first_pass_valid,
            "valid_after_repair": graph.valid_after_repair,
            "non_empty": graph.non_empty,
            "fallback": graph.fallback_reason is not None,
        },
        "stages": {name: asdict(graph.stages[name]) for name in GRAPH_STAGES
                   if name in graph.stages},
        **({"global_search": _question_global(graph.global_search)}
           if graph.global_search is not None else {}),
    }


def _question_global(search: GlobalSearch) -> dict[str, object]:
    """One question's map calls with every point, kept or dropped, and its reduce input."""
    return {
        "map_calls": [
            {"community_id": c.community_id, "rank": c.rank, "similarity": c.similarity,
             "shown": list(c.shown), "outcome": c.outcome, "cost": asdict(c.cost),
             "points": [{"text": p.text, "score": p.score, "citations": list(p.citations),
                         "kept": p.kept, "drop_reason": p.drop_reason, "in_reduce": p.in_reduce}
                        for p in c.points]}
            for c in search.calls],
        "reduce": {"called": search.reduce_called, "points": search.reduce_points,
                   "input_tokens": search.reduce_input_tokens,
                   "cut_by_budget": search.cut_by_budget},
    }


def graph_cell(results: Sequence[ArmResult]) -> dict[str, int] | None:
    """Per-class counts for a graph arm's questions; None when no result has graph details."""
    graphed = [(r, r.graph) for r in results if r.graph is not None]
    if not graphed:
        return None
    searches = [graph.global_search for _, graph in graphed if graph.global_search is not None]
    return {
        "n": len(graphed),
        "no_anchor": sum(graph.no_anchor for _, graph in graphed),
        "empty_retrieval": sum(not r.retrieved for r, _ in graphed),
        "not_found": sum(r.answer.status == "not_found" for r, _ in graphed),
        **({"no_community": sum(not s.calls for s in searches),
            "no_reduce": sum(not s.reduce_called for s in searches),
            "map_calls_unusable": sum(c.outcome != "ok" for s in searches for c in s.calls),
            "map_points": sum(len(s.points) for s in searches),
            "map_points_dropped": sum(not p.kept for s in searches for p in s.points)}
           if searches else {}),
    }
