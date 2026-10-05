"""Retrieval metrics from chunk IDs (Step 5, ticket 03), on real eval records.

Expected values are computed by hand in each test's comment.
"""
from pathlib import Path

import pytest

from eval.metrics import precision_at_k, recall_at_k
from eval.schema import EvalRecord, load_records

AGENT_DRAFTED = Path(__file__).resolve().parents[1] / "eval" / "agent_drafted_set.jsonl"
_RECORDS = {r.id: r for r in load_records(AGENT_DRAFTED)}


def _record(record_id: str) -> EvalRecord:
    return _RECORDS[record_id]


def _filler(n: int) -> list[str]:
    """Chunk IDs that are neither gold nor hard negatives for the records used here."""
    return [f"0001045810-26-000075:{i:04d}" for i in range(n)]


def test_two_gold_chunks_one_in_top_five_one_at_rank_seven():
    # q0113 (multi_hop): gold A = AMD FY2025 :0076, B = Intel FY2025 :0036.
    # Ranking: hard negative, A, 4 fillers, B, 3 fillers.
    # recall@5 = 1/2, precision@5 = 1/5, recall@10 = 2/2, precision@10 = 2/10.
    record = _record("q0113")
    gold_a, gold_b = record.gold_chunk_ids
    negative = record.hard_negatives[0].chunk_id
    filler = _filler(7)
    retrieved = [negative, gold_a, *filler[:4], gold_b, *filler[4:]]
    assert len(retrieved) == 10
    gold = record.gold_chunk_ids
    assert recall_at_k(retrieved, gold, 5) == pytest.approx(0.5)
    assert precision_at_k(retrieved, gold, 5) == pytest.approx(0.2)
    assert recall_at_k(retrieved, gold, 10) == pytest.approx(1.0)
    assert precision_at_k(retrieved, gold, 10) == pytest.approx(0.2)


def test_four_gold_chunks_two_retrieved():
    # q0028 (global): four gold chunks; the first and third come back at ranks 1 and 4.
    # recall@5 = 2/4, precision@5 = 2/5, recall@10 = 2/4, precision@10 = 2/10.
    record = _record("q0028")
    gold = record.gold_chunk_ids
    assert len(gold) == 4
    filler = _filler(8)
    retrieved = [gold[0], filler[0], filler[1], gold[2], *filler[2:]]
    assert recall_at_k(retrieved, gold, 5) == pytest.approx(0.5)
    assert precision_at_k(retrieved, gold, 5) == pytest.approx(0.4)
    assert recall_at_k(retrieved, gold, 10) == pytest.approx(0.5)
    assert precision_at_k(retrieved, gold, 10) == pytest.approx(0.2)


def test_precision_divides_by_k_when_fewer_chunks_come_back():
    # q0113: only 3 chunks retrieved, one of them gold.  precision@5 = 1/5, not 1/3.
    record = _record("q0113")
    retrieved = [record.gold_chunk_ids[0], *_filler(2)]
    assert precision_at_k(retrieved, record.gold_chunk_ids, 5) == pytest.approx(0.2)
    assert recall_at_k(retrieved, record.gold_chunk_ids, 5) == pytest.approx(0.5)


def test_no_gold_chunk_retrieved_scores_zero():
    record = _record("q0113")
    retrieved = [n.chunk_id for n in record.hard_negatives] + _filler(8)
    assert recall_at_k(retrieved, record.gold_chunk_ids, 10) == 0.0
    assert precision_at_k(retrieved, record.gold_chunk_ids, 10) == 0.0


def test_a_record_without_gold_chunks_has_no_retrieval_score():
    decline = next(r for r in _RECORDS.values() if r.class_ == "decline")
    with pytest.raises(ValueError, match="no gold"):
        recall_at_k(_filler(10), decline.gold_chunk_ids, 10)
    with pytest.raises(ValueError, match="no gold"):
        precision_at_k(_filler(10), decline.gold_chunk_ids, 10)


def test_k_must_be_positive():
    record = _record("q0113")
    with pytest.raises(ValueError, match="k must be"):
        recall_at_k(_filler(10), record.gold_chunk_ids, 0)
