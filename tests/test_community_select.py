"""Choosing communities and their chunks for graph_global, the pure part (Step 12 ticket 05).

Report vectors are fakes whose cosine with the query (the first axis) is the
similarity each test states, so the expected ranking follows from the rule:
cosine, best first, ties by community_id, keeping only communities with
evidence in the filtered filings.
"""
import pytest

from retrieve.arm import RetrievedChunk
from retrieve.community_select import (
    CommunityIndex,
    ReportedCommunity,
    SelectedCommunity,
    ranked_list,
    report_text,
    shown_chunks,
)
from tests.graph_local_data import QUERY_VECTOR, at_similarity

NVDA_K = "0001045810-26-000021"
NVDA_Q = "0001045810-26-000075"
AMD_K = "0000002488-26-000018"


def _community(community_id: str, *chunk_ids: str) -> ReportedCommunity:
    return ReportedCommunity(community_id, f"{community_id}: summary",
                             {c: c.split(":")[0] for c in chunk_ids})


COMMUNITIES = (
    _community("lower-a", f"{NVDA_K}:0012", f"{NVDA_K}:0006"),
    _community("lower-b", f"{NVDA_Q}:0035", f"{NVDA_K}:0061"),
    _community("lower-c", f"{AMD_K}:0014"),
    _community("lower-d", f"{NVDA_K}:0013"),
)


def _index(similarities: dict[str, float]) -> CommunityIndex:
    vectors = {c.text: at_similarity(similarities[c.community_id]) for c in COMMUNITIES}
    return CommunityIndex(COMMUNITIES, vectors)


def _ids(selected) -> list[str]:
    return [s.community_id for s in selected]


def test_report_text_is_title_then_summary():
    assert report_text("TSMC and foundries", "NVIDIA relies on TSMC [x:0001].") == \
        "TSMC and foundries: NVIDIA relies on TSMC [x:0001]."


def test_communities_rank_by_cosine_and_cut_to_n():
    index = _index({"lower-a": 0.2, "lower-b": 0.9, "lower-c": 0.5, "lower-d": 0.7})
    selected = index.select(QUERY_VECTOR, None, n=3)
    assert _ids(selected) == ["lower-b", "lower-d", "lower-c"]
    assert [s.similarity for s in selected] == pytest.approx([0.9, 0.7, 0.5])


def test_equal_similarities_tie_by_community_id():
    index = _index({"lower-a": 0.4, "lower-b": 0.4, "lower-c": 0.4, "lower-d": 0.9})
    assert _ids(index.select(QUERY_VECTOR, None, n=4)) == [
        "lower-d", "lower-a", "lower-b", "lower-c"]


def test_the_filter_drops_communities_without_evidence_in_its_filings():
    index = _index({"lower-a": 0.2, "lower-b": 0.9, "lower-c": 0.95, "lower-d": 0.7})
    # Only NVIDIA's 10-K: AMD's community goes, and the next ones move up to fill N.
    selected = index.select(QUERY_VECTOR, [NVDA_K], n=3)
    assert _ids(selected) == ["lower-b", "lower-d", "lower-a"]


def test_a_selected_community_keeps_only_its_chunks_inside_the_filter():
    index = _index({"lower-a": 0.2, "lower-b": 0.9, "lower-c": 0.5, "lower-d": 0.7})
    (selected,) = index.select(QUERY_VECTOR, [NVDA_Q], n=3)
    assert selected == SelectedCommunity("lower-b", pytest.approx(0.9), (f"{NVDA_Q}:0035",))


def test_no_filter_keeps_every_evidence_chunk_sorted():
    index = _index({"lower-a": 0.2, "lower-b": 0.9, "lower-c": 0.5, "lower-d": 0.7})
    selected = index.select(QUERY_VECTOR, None, n=1)
    assert selected[0].chunk_ids == (f"{NVDA_K}:0061", f"{NVDA_Q}:0035")


def test_a_filter_no_community_matches_selects_none():
    index = _index({"lower-a": 0.2, "lower-b": 0.9, "lower-c": 0.5, "lower-d": 0.7})
    assert index.select(QUERY_VECTOR, ["0000050863-26-000011"], n=4) == ()


def test_a_report_text_without_a_vector_is_refused():
    vectors = {c.text: at_similarity(0.5) for c in COMMUNITIES[1:]}
    with pytest.raises(ValueError, match="no vector"):
        CommunityIndex(COMMUNITIES, vectors)


def test_shown_chunks_rank_by_cosine_ties_by_chunk_id_and_cut_to_the_limit():
    scores = {"a:0001": 0.3, "a:0002": 0.9, "a:0003": 0.5, "a:0004": 0.5, "a:0005": 0.1}
    shown = shown_chunks(tuple(scores), scores, limit=3)
    assert shown == (RetrievedChunk("a:0002", 0.9), RetrievedChunk("a:0003", 0.5),
                     RetrievedChunk("a:0004", 0.5))


def test_shown_chunks_refuse_a_chunk_with_no_vector():
    with pytest.raises(ValueError, match="a:0002"):
        shown_chunks(("a:0001", "a:0002"), {"a:0001": 0.4}, limit=6)


def test_the_ranked_list_follows_community_rank_then_cosine_without_repeats():
    first = (RetrievedChunk("a:0002", 0.9), RetrievedChunk("a:0001", 0.3))
    second = (RetrievedChunk("b:0001", 0.95), RetrievedChunk("a:0002", 0.9),
              RetrievedChunk("b:0002", 0.2))
    assert [c.chunk_id for c in ranked_list((first, second))] == [
        "a:0002", "a:0001", "b:0001", "b:0002"]
