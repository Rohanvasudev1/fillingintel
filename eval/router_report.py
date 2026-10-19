"""What a router run adds to the results file (Step 13).

The header gains ``routing_map`` and a ``router`` section: the classifier's
model, effort, prompt and price, the routing map with its SHA-256, and each
arm the router opened with its provenance as that arm's own run records it.
Each question record gains its routing (predicted class, reason, map, chosen
arm or ``decline``, effort, prompt and the classifier call's cost).  An arm's
run file is unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict

from eval import graph_report, operational
from retrieve.arm import ArmConfig, RouterConfig, Routing
from retrieve.pricing import price_table
from retrieve.route_classifier import ROUTE_PRICES

ROUTER_NOTE = ("the router is not an arm: it classifies each question, declines it or hands "
               "it to the arm the routing map names")
DEFINITIONS = {
    **operational.ROUTER_DEFINITIONS,
    "router": (
        "router runs only: each question record's routing holds the predicted class "
        "(lookup, local, multi_hop, global or decline), the classifier's reason, the routing "
        "map, the arm chosen (decline: the fixed decline sentence, with no retrieval and no "
        "answer call), the effort and prompt, and the classifier call's cost; the header's "
        "router.arms holds each arm the router opened, as that arm's own run records it"
    ),
}


# The run header fields arm_provenance fills, in a plain arm's own run header.
PROVENANCE_KEYS: tuple[str, ...] = ("models", "efforts", "answer_prompt", "answer_max_tokens",
                                    "k", "chunker_version", "graph")


def arm_provenance(config: ArmConfig) -> dict[str, object]:
    """An arm's settings and what it read, as its own run header records them."""
    return {
        "models": dict(config.models),
        "efforts": dict(config.efforts),
        "answer_prompt": {"version": config.answer_prompt.version,
                          "sha256": config.answer_prompt.sha256,
                          "path": config.answer_prompt.path},
        "answer_max_tokens": config.answer_max_tokens,
        "k": config.k,
        "chunker_version": config.chunker_version,
        "graph": graph_report.header_graph(config.graph) if config.graph is not None else None,
    }


def router_header(router: RouterConfig) -> dict[str, object]:
    return {
        "note": ROUTER_NOTE,
        "model": router.model,
        "effort": router.effort,
        "max_tokens": router.max_tokens,
        "prompt": {"version": router.prompt_version, "sha256": router.prompt_sha256,
                   "path": router.prompt_path},
        "map": {"name": router.map_name, "arms": dict(router.map), "sha256": router.map_sha256,
                "file": router.map_file},
        "prices": price_table([router.model], ROUTE_PRICES),
        "arms": {name: arm_provenance(c) for name, c in sorted(router.arms.items())},
    }


def question_routing(routing: Routing) -> dict[str, object]:
    return asdict(routing)


def inner_graph_definitions(arms: Mapping[str, ArmConfig]) -> dict[str, str]:
    """The graph definitions of the arms a router opened; the global stage text wins over
    the entity arms' when both families ran, and names both families' stages."""
    graphs = [c.graph for c in arms.values() if c.graph is not None]
    if not graphs:
        return {}
    entity = any(g.global_search is None for g in graphs)
    global_ = any(g.global_search is not None for g in graphs)
    definitions = dict(graph_report.DEFINITIONS)
    if entity:
        definitions |= operational.GRAPH_DEFINITIONS
    if global_:
        definitions |= graph_report.GLOBAL_DEFINITIONS
        stages = operational.GLOBAL_DEFINITIONS["graph_stages"]
        definitions["graph_stages"] = (
            f"{operational.GRAPH_DEFINITIONS['graph_stages']}; {stages}" if entity else stages)
    return definitions
