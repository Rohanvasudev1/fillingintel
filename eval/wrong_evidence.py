"""Wrong evidence from chunk IDs, with no LLM (Step 5, ticket 06).

A hard negative is wrong evidence for a question when it ranks above a gold
chunk among the retrieved chunks, or when the answer cites it.  "Above a gold
chunk" means above at least one: a gold chunk that was not retrieved ranks below
every retrieved chunk, so any retrieved hard negative counts while some gold
chunk is missing.  Citations are those of the raw answer, kept or dropped,
because a dropped sentence still shows the model resting on that chunk.
"""
from __future__ import annotations

import random
from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import get_args

from eval.bootstrap import RngFor, mean_interval, ratio_interval
from eval.schema import EvalRecord, Relation
from retrieve.answer import Answer

DEFINITIONS = {
    "wrong_evidence": (
        "questions where a hard negative ranks above at least one gold chunk in the retrieved "
        "list (a gold chunk not retrieved ranks below all), or is cited anywhere in the raw "
        "answer / questions with hard negatives; by_relation counts a question under each "
        "hard-negative label that triggered, over the questions with a negative of that label; "
        "share_of_wrong is the share of wrong questions that label triggered (H3)"
    ),
}


@dataclass(frozen=True)
class WrongEvidenceHit:
    """One hard negative that counted, and why."""

    chunk_id: str
    relation: Relation
    rank: int | None  # 1-based position in the retrieved list; None if not retrieved
    above_gold: bool
    cited: bool


def has_hard_negatives(record: EvalRecord) -> bool:
    """Whether *record* can be scored for wrong evidence at all."""
    return bool(record.hard_negatives)


def wrong_evidence_hits(
    record: EvalRecord, retrieved: Sequence[str], cited: Collection[str]
) -> tuple[WrongEvidenceHit, ...]:
    """The hard negatives of *record* that ranked above a gold chunk or were cited, by rank."""
    ranks = {cid: rank for rank, cid in reversed(list(enumerate(retrieved, start=1)))}
    lowest_gold = max((ranks.get(g, len(retrieved) + 1) for g in record.gold_chunk_ids), default=0)
    candidates = (
        WrongEvidenceHit(
            chunk_id=n.chunk_id,
            relation=n.relation,
            rank=ranks.get(n.chunk_id),
            above_gold=n.chunk_id in ranks and ranks[n.chunk_id] < lowest_gold,
            cited=n.chunk_id in cited,
        )
        for n in record.hard_negatives
    )
    hits = (h for h in candidates if h.above_gold or h.cited)
    return tuple(sorted(hits, key=lambda h: (h.rank is None, h.rank or 0)))


def cited_in(answer: Answer) -> set[str]:
    """Every chunk ID the raw answer cites, in kept and dropped sentences."""
    check = answer.citation_check
    return {cid for s in (*check.kept, *check.dropped) for cid in s.citations}


def hit_record(hit: WrongEvidenceHit) -> dict[str, object]:
    return asdict(hit)


def _rate(flags: Sequence[bool], rng: random.Random) -> dict[str, object]:
    values = [float(f) for f in flags]
    return {
        "n": len(values),
        "wrong": sum(flags),
        "rate": fmean(values) if values else None,
        "interval": mean_interval(values, rng),
    }


def _label_rate(
    applicable: Sequence[tuple[EvalRecord, Sequence[WrongEvidenceHit]]],
    relation: Relation,
    rng_for: RngFor,
) -> dict[str, object] | None:
    """The rate for one label, and its share of all wrong questions; None if no record has it."""
    flags = [any(h.relation == relation for h in hits)
             for record, hits in applicable
             if any(n.relation == relation for n in record.hard_negatives)]
    if not flags:
        return None
    pairs = [(float(any(h.relation == relation for h in hits)), float(bool(hits)))
             for _, hits in applicable]
    wrong = sum(d for _, d in pairs)
    return {
        **_rate(flags, rng_for(f"wrong_evidence.{relation}")),
        "share_of_wrong": sum(n for n, _ in pairs) / wrong if wrong else None,
        "share_of_wrong_interval": ratio_interval(
            pairs, rng_for(f"wrong_evidence.{relation}.share_of_wrong")),
    }


def wrong_evidence_cell(
    scored: Sequence[tuple[EvalRecord, Sequence[WrongEvidenceHit]]], rng_for: RngFor
) -> dict[str, object]:
    """The wrong-evidence rate over records with hard negatives, overall and per label."""
    applicable = [(record, hits) for record, hits in scored if has_hard_negatives(record)]
    labels = {r: _label_rate(applicable, r, rng_for) for r in get_args(Relation)}
    return {**_rate([bool(hits) for _, hits in applicable], rng_for("wrong_evidence")),
            "by_relation": {r: rate for r, rate in labels.items() if rate is not None}}
