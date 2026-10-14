"""Flattening a guarded query's rows into graph_traversal's ranked chunks (Step 11 ticket 06).

ADR-0007: rows are read in order, chunk IDs in each row in order; the first
appearance of a chunk counts, chunks outside the question filter or unknown to
the corpus are dropped, and the list is cut to k.
"""
import pytest

from retrieve.cypher_rows import rank_rows

K10 = "0001045810-26-000021"
Q2 = "0001045810-26-000075"
AMD = "0000002488-26-000018"
KNOWN = frozenset({f"{K10}:0001", f"{K10}:0002", f"{K10}:0003", f"{Q2}:0001", f"{AMD}:0007"})


def _ids(retrieved) -> list[str]:
    return [r.chunk_id for r in retrieved]


def test_rows_flatten_in_order_and_keep_each_chunks_first_appearance():
    rows = [{"chunk_ids": [f"{K10}:0002", f"{K10}:0001"]},
            {"chunk_ids": [f"{K10}:0001", f"{AMD}:0007", f"{K10}:0002"]}]
    assert _ids(rank_rows(rows, None, KNOWN, 10)) == [f"{K10}:0002", f"{K10}:0001",
                                                      f"{AMD}:0007"]


def test_scores_fall_with_rank():
    rows = [{"chunk_ids": [f"{K10}:0002", f"{K10}:0001", f"{AMD}:0007"]}]
    assert [r.score for r in rank_rows(rows, None, KNOWN, 10)] == pytest.approx([1.0, 0.5, 1 / 3])


def test_chunks_outside_the_filter_are_dropped_before_the_cut():
    rows = [{"chunk_ids": [f"{AMD}:0007", f"{Q2}:0001", f"{K10}:0003", f"{K10}:0001"]}]
    assert _ids(rank_rows(rows, (K10,), KNOWN, 1)) == [f"{K10}:0003"]


def test_chunk_ids_the_corpus_does_not_hold_are_dropped():
    rows = [{"chunk_ids": [f"{K10}:9999", "not a chunk", f"{K10}:0001"]}]
    assert _ids(rank_rows(rows, None, KNOWN, 10)) == [f"{K10}:0001"]


def test_the_list_is_cut_to_k():
    rows = [{"chunk_ids": [f"{K10}:0001"]}, {"chunk_ids": [f"{K10}:0002", f"{K10}:0003"]}]
    assert _ids(rank_rows(rows, None, KNOWN, 2)) == [f"{K10}:0001", f"{K10}:0002"]


def test_no_rows_or_empty_lists_give_nothing():
    assert rank_rows([], None, KNOWN, 10) == ()
    assert rank_rows([{"chunk_ids": []}], None, KNOWN, 10) == ()


def test_traversal_chunks_carry_no_confidence():
    (chunk,) = rank_rows([{"chunk_ids": [f"{K10}:0001"]}], None, KNOWN, 10)
    assert chunk.confidence is None
