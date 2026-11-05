"""The answer trace for each picker option, and the Phoenix link (Step 14)."""
from __future__ import annotations

from types import MappingProxyType

from retrieve.arm import (
    Anchor,
    ChunkSupport,
    CypherAttempt,
    GlobalSearch,
    GraphRetrieval,
    MapCall,
    MapPoint,
    StageCost,
    SupportEdge,
)
from tests.ui_apptest import ask_with, page_text
from tests.ui_fakes import CHUNK, arm_result, routing
from ui.communities import CommunityInfo

COMMUNITY = "lower:0123456789ab"


def trace_text(at) -> str:
    expander = next(e for e in at.expander if e.label.startswith("Answer trace"))
    return " ".join(e.value for e in [*expander.markdown, *expander.caption, *expander.code])


def test_vector_shows_its_ranked_chunks_with_scores():
    at = ask_with(arm_result())

    expander = next(e for e in at.expander if e.label.startswith("Answer trace"))
    ranked = expander.dataframe[0].value
    assert list(ranked["chunk"]) == [CHUNK]
    assert list(ranked["score"]) == ["0.9000"]


def test_the_router_shows_its_class_reason_and_chosen_arm():
    at = ask_with(arm_result(routing=routing(arm="vector")), option="router (dev-best)")

    shown = trace_text(at)
    assert "lookup" in shown and "a single figure" in shown and "vector" in shown


ANCHOR_KEY = 'Product:h100 "sxm" \\ gpu'


def local_graph() -> GraphRetrieval:
    edge = SupportEdge("OFFERS", "Segment", "Segment:1045810:compute", "Compute & Networking",
                       outgoing=False)
    return GraphRetrieval(
        stages=MappingProxyType({"anchor_linking": StageCost(ms=40.0)}),
        anchors=(Anchor(ANCHOR_KEY, "Product", 0.81, "embedding"),), hops=1,
        support=(ChunkSupport(CHUNK, "Product", ANCHOR_KEY, evidenced=True, edges=(edge,),
                              neighbours=(), score=1.62),))


def test_graph_local_shows_its_anchors_and_a_drawing_with_escaped_labels():
    at = ask_with(arm_result(graph=local_graph()), option="graph_local")

    expander = next(e for e in at.expander if e.label.startswith("Answer trace"))
    anchors = expander.dataframe[0].value
    assert list(anchors["key"]) == [ANCHOR_KEY]
    assert list(anchors["matched by"]) == ["embedding"]
    dot = at.get("graphviz_chart")[0].proto.spec
    assert 'Product:h100 \\"sxm\\" \\\\ gpu' in dot  # quotes and backslash escaped for DOT
    assert ANCHOR_KEY not in dot
    assert "OFFERS" in dot and CHUNK in dot and "Compute & Networking" in dot


def test_graph_traversal_shows_each_cypher_attempt_and_the_fallback():
    graph = GraphRetrieval(cypher_attempts=(
        CypherAttempt("MATCH (n) RETURN n", "invalid", rule="chunk_ids_column",
                      message="no chunk_ids column"),
        CypherAttempt("MATCH (c:Chunk) RETURN [c.chunk_id] AS chunk_ids", "empty", rows=0),
    ), fallback_reason="empty", anchors=local_graph().anchors, support=local_graph().support)
    at = ask_with(arm_result(graph=graph), option="graph_traversal")

    shown = trace_text(at)
    assert "MATCH (n) RETURN n" in shown and "[c.chunk_id] AS chunk_ids" in shown
    assert "invalid" in shown and "chunk_ids_column" in shown and "0 rows" in shown
    assert "fell back to graph_local (empty)" in shown


def test_graph_global_shows_its_communities_and_map_points():
    point = MapPoint(COMMUNITY, "Export rules tightened.", 80, (CHUNK,), in_reduce=True)
    dropped = MapPoint(COMMUNITY, "Unsourced.", 30, (), drop_reason="no_citation")
    call = MapCall(COMMUNITY, 1, 0.71, (CHUNK,), "ok", (point, dropped), StageCost(ms=900.0))
    graph = GraphRetrieval(global_search=GlobalSearch((call,), reduce_points=1,
                                                      reduce_input_tokens=40, cut_by_budget=0))
    info = CommunityInfo(COMMUNITY, "Export controls", "Rules on chip exports to China.")
    at = ask_with(arm_result(graph=graph), option="graph_global",
                  communities={COMMUNITY: info})

    shown = trace_text(at)
    assert "Export controls" in shown and "Rules on chip exports to China." in shown
    expander = next(e for e in at.expander if e.label.startswith("Answer trace"))
    points = expander.dataframe[0].value
    assert list(points["score"]) == [80, 30]
    assert list(points["in reduce"]) == ["yes", "no"]
    assert list(points["dropped"]) == ["", "no citation"]


def test_the_phoenix_link_shows_only_when_tracing_is_on():
    traced = ask_with(arm_result(), trace_id="ab" * 16)
    assert f"http://localhost:6006/redirects/traces/{'ab' * 16}" in page_text(traced)

    assert "redirects/traces" not in page_text(ask_with(arm_result()))
