"""The answer trace: what the option did to find its chunks (Step 14).

Display only: nothing here is cited, and nothing changes the answer (ADR-0007).
Every model- or graph-written string is escaped before Streamlit renders it.
"""
from __future__ import annotations

from collections.abc import Mapping

import streamlit as st

from retrieve.arm import ArmResult, GlobalSearch, GraphRetrieval, Routing
from ui.backend import UiAnswer
from ui.communities import CommunityInfo
from ui.render import citation_numbers, md_escape, plural, support_dot

MAP_DROPS: Mapping[str, str] = {"no_citation": "no citation",
                                "citation_not_shown": "cites a chunk not shown",
                                "bad_score": "score not 0 to 100"}


def show_trace(answer: UiAnswer) -> None:
    result = answer.result
    with st.expander(f"Answer trace ({answer.option})"):
        if answer.trace_url:
            st.markdown(f"[Open this question's spans in Phoenix]({answer.trace_url})")
        if result.routing is not None:
            _route(result.routing)
        graph = result.graph
        if graph is not None and graph.cypher_attempts:
            _cypher(graph)
        if graph is not None and (graph.anchors or graph.no_anchor):
            _local(graph, citation_numbers(result.answer.citation_check.kept))
        if graph is not None and graph.global_search is not None:
            _global(graph.global_search, answer.communities)
        _ranked(result)


def _route(routing: Routing) -> None:
    chosen = ("declined it: no retrieval and no answer call" if routing.declined
              else f"sent it to **{md_escape(routing.arm)}**")
    st.markdown(f"**Route.** The classifier read this as **{md_escape(routing.predicted_class)}**"
                f" and the {md_escape(routing.map_name)} map {chosen}.")
    st.caption(f"Reason: {md_escape(routing.reason)} ({md_escape(routing.prompt_version)}, "
               f"effort {md_escape(routing.effort)})")


def _cypher(graph: GraphRetrieval) -> None:
    st.markdown("**Generated Cypher.**")
    for i, attempt in enumerate(graph.cypher_attempts, start=1):
        kind = "query" if i == 1 else "repair"
        st.markdown(f"Attempt {i} ({kind}): **{attempt.outcome}**, {attempt.rows} rows")
        if attempt.rule or attempt.message:
            rule = f"`{attempt.rule}` " if attempt.rule else ""
            st.caption(rule + md_escape(attempt.message or ""))
        st.code(attempt.query, language="cypher")
    if graph.fallback_reason is not None:
        st.markdown(f"No query returned chunks, so the arm fell back to graph_local "
                    f"({graph.fallback_reason}).")


def _local(graph: GraphRetrieval, numbers: Mapping[str, int]) -> None:
    hops = f", {graph.hops} hop" if graph.hops else ""
    st.markdown(f"**Anchors** (graph_local{hops}).")
    if graph.no_anchor:
        st.markdown("No anchor had evidence in the filtered filings, so no chunk was found.")
        return
    st.dataframe([{"label": a.label, "key": a.key, "similarity": f"{a.similarity:.3f}",
                   "matched by": a.matched_by} for a in graph.anchors], hide_index=True)
    if graph.support:
        st.caption("Which anchor and edges gave each kept chunk its score.")
        st.graphviz_chart(support_dot(graph.support, numbers))


def _global(search: GlobalSearch, communities: Mapping[str, CommunityInfo]) -> None:
    st.markdown("**Communities read by the map step.**")
    for call in search.calls:
        info = communities.get(call.community_id)
        title = info.title if info is not None and info.title else call.community_id
        st.markdown(f"{call.rank}. **{md_escape(title)}** (similarity {call.similarity:.3f}, "
                    f"{plural(len(call.shown), 'chunk')} shown, reply {call.outcome})")
        if info is not None and info.summary:
            st.caption(md_escape(info.summary))
    st.dataframe([{"community": call.rank, "score": point.score, "point": point.text,
                   "citations": ", ".join(point.citations),
                   "in reduce": "yes" if point.in_reduce else "no",
                   "dropped": MAP_DROPS.get(point.drop_reason or "", "")}
                  for call in search.calls for point in call.points], hide_index=True)
    if search.reduce_called:
        st.caption(f"{search.reduce_points} points went to the reduce call; "
                   f"{search.cut_by_budget} above 0 were cut by its token budget.")
    else:
        st.caption("No point scored above 0, so the answer is not found, with no reduce call.")


def _ranked(result: ArmResult) -> None:
    if not result.retrieved:
        st.markdown("No chunks were retrieved.")
        return
    st.markdown("**Retrieved chunks**, best first, as the answer model saw them.")
    st.dataframe([{"rank": i, "chunk": c.chunk_id, "score": f"{c.score:.4f}",
                   "confidence": c.confidence or ""}
                  for i, c in enumerate(result.retrieved, start=1)], hide_index=True)
