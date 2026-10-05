"""Wrong evidence from chunk IDs (Step 5, ticket 06), on real eval records.

A hard negative counts as wrong evidence when it ranks above a gold chunk or is
cited.  A gold chunk that was not retrieved ranks below every retrieved chunk.
Expected values are computed by hand in each test's comment.
"""
from pathlib import Path

from eval.schema import load_records
from eval.wrong_evidence import WrongEvidenceHit, has_hard_negatives, wrong_evidence_hits

AGENT_DRAFTED = Path(__file__).resolve().parents[1] / "eval" / "agent_drafted_set.jsonl"
_RECORDS = {r.id: r for r in load_records(AGENT_DRAFTED)}
Q0072, Q0113, Q0028 = _RECORDS["q0072"], _RECORDS["q0113"], _RECORDS["q0028"]


def _filler(n: int) -> list[str]:
    """Chunk IDs that are neither gold nor hard negatives for the records used here."""
    return [f"0001045810-26-000075:{i:04d}" for i in range(n)]


def test_a_hard_negative_above_the_first_gold_chunk_counts():
    # q0113: hard negative 0 at rank 1, gold at ranks 2 and 7; hard negative 1 not retrieved
    negative = Q0113.hard_negatives[0]
    g0, g1 = Q0113.gold_chunk_ids
    f = _filler(7)
    retrieved = [negative.chunk_id, g0, *f[:4], g1, *f[4:]]
    assert wrong_evidence_hits(Q0113, retrieved, cited=()) == (
        WrongEvidenceHit(negative.chunk_id, "same_company_other_period", rank=1,
                         above_gold=True, cited=False),
    )


def test_a_hard_negative_between_two_gold_chunks_counts():
    # q0113: gold at rank 1, hard negative 1 at rank 2, gold at rank 5: above the second gold
    negative = Q0113.hard_negatives[1].chunk_id
    g0, g1 = Q0113.gold_chunk_ids
    f = _filler(7)
    retrieved = [g0, negative, *f[:2], g1, *f[2:]]
    (hit,) = wrong_evidence_hits(Q0113, retrieved, cited=())
    assert (hit.chunk_id, hit.rank, hit.above_gold, hit.cited) == (negative, 2, True, False)


def test_a_hard_negative_below_every_gold_chunk_does_not_count():
    # q0072: gold at rank 1, its hard negative at rank 3, not cited
    retrieved = [Q0072.gold_chunk_ids[0], *_filler(1), Q0072.hard_negatives[0].chunk_id,
                 *_filler(7)[1:]]
    assert wrong_evidence_hits(Q0072, retrieved, cited=[Q0072.gold_chunk_ids[0]]) == ()


def test_a_retrieved_hard_negative_counts_when_the_gold_chunk_was_not_retrieved():
    # q0072: hard negative at rank 5, gold missing from the top 10, so it ranks below
    negative = Q0072.hard_negatives[0].chunk_id
    f = _filler(9)
    retrieved = [*f[:4], negative, *f[4:]]
    (hit,) = wrong_evidence_hits(Q0072, retrieved, cited=())
    assert (hit.rank, hit.above_gold, hit.cited) == (5, True, False)


def test_a_cited_hard_negative_counts_even_when_it_ranks_below_gold():
    # q0028: every gold chunk in ranks 1-4, hard negative 1 at rank 5 and cited
    negative = Q0028.hard_negatives[1]
    retrieved = [*Q0028.gold_chunk_ids, negative.chunk_id, *_filler(5)]
    assert wrong_evidence_hits(Q0028, retrieved, cited=[negative.chunk_id]) == (
        WrongEvidenceHit(negative.chunk_id, "same_company_other_period", rank=5,
                         above_gold=False, cited=True),
    )


def test_a_cited_hard_negative_that_was_not_retrieved_counts():
    # q0028: hard negative 0 cited from outside the retrieved chunks, so it has no rank
    negative = Q0028.hard_negatives[0]
    retrieved = [*Q0028.gold_chunk_ids, *_filler(6)]
    (hit,) = wrong_evidence_hits(Q0028, retrieved, cited=[negative.chunk_id])
    assert (hit.relation, hit.rank, hit.above_gold, hit.cited) == (
        "same_company_same_filing", None, False, True)


def test_both_hard_negatives_are_reported_in_rank_order():
    # q0028: hard negative 1 at rank 1, hard negative 0 at rank 3, no gold retrieved
    h0, h1 = (n.chunk_id for n in Q0028.hard_negatives)
    f = _filler(8)
    retrieved = [h1, f[0], h0, *f[1:]]
    hits = wrong_evidence_hits(Q0028, retrieved, cited=())
    assert [(h.chunk_id, h.rank) for h in hits] == [(h1, 1), (h0, 3)]


def test_records_without_hard_negatives_are_not_scored():
    assert has_hard_negatives(Q0072)
    assert not has_hard_negatives(_RECORDS["q0004"])  # decline
    assert wrong_evidence_hits(_RECORDS["q0004"], _filler(10), cited=()) == ()
