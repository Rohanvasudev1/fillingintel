"""What a graph arm adds to the results file (Step 11).

A vector run has none of these fields, so its file is unchanged.  A graph run's
header records the graph it read, each question record holds its anchors, Cypher
attempts and stage costs, and each cell counts the questions that linked no
anchor, retrieved nothing or were answered "not found", so a good unanswerable
score can be read for what it is.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict

from retrieve.arm import GRAPH_STAGES, ArmResult, GraphProvenance, GraphRetrieval

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
    }


def graph_cell(results: Sequence[ArmResult]) -> dict[str, int] | None:
    """Per-class counts for a graph arm's questions; None when no result has graph details."""
    graphed = [(r, r.graph) for r in results if r.graph is not None]
    if not graphed:
        return None
    return {
        "n": len(graphed),
        "no_anchor": sum(graph.no_anchor for _, graph in graphed),
        "empty_retrieval": sum(not r.retrieved for r, _ in graphed),
        "not_found": sum(r.answer.status == "not_found" for r, _ in graphed),
    }
