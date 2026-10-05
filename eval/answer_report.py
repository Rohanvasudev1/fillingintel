"""Per-question answer records and per-cell answer counts (Step 5).

Structural citation validity is deterministic: it counts the chunk IDs cited in
the model's raw answer, kept or dropped, and how many of them were retrieved.
Whether a cited chunk supports its sentence is a judged metric (ticket 07).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict

from eval.bootstrap import RngFor, ratio_interval
from retrieve.answer import Answer

DEFINITIONS = {
    "structural_citation_validity": (
        "cited chunk IDs that were among the retrieved chunks / cited chunk IDs, counted over "
        "every sentence of the raw answer, kept or dropped; null when nothing was cited"
    ),
    "dropped_share": "sentences dropped by citation enforcement / sentences in the raw answers",
    "status": (
        "the answer's status line: answered, declined, not_found; no_status if the line was "
        "missing; refused if the API refused"
    ),
    "truncated": "answers whose stop_reason is max_tokens",
}


def answer_record(answer: Answer) -> dict[str, object]:
    """One answer, with every kept and dropped sentence and its citations."""
    check = answer.citation_check
    return {
        "status": answer.status,
        "text": answer.text,
        "raw_text": answer.raw_text,
        "kept": [{"text": s.text, "citations": list(s.citations)} for s in check.kept],
        "dropped": [
            {"text": d.text, "citations": list(d.citations), "reason": d.reason}
            for d in check.dropped
        ],
        "sentences": check.sentences,
        "citations": check.citations,
        "citations_retrieved": check.citations_retrieved,
        "model": answer.model,
        "message_id": answer.message_id,
        "stop_reason": answer.stop_reason,
        "refusal_category": answer.refusal_category,
        "usage": asdict(answer.usage),
        "from_cache": answer.from_cache,
    }


def answer_cell(answers: Sequence[Answer], rng_for: RngFor) -> dict[str, object]:
    """Status counts, dropped sentences and structural citation validity over *answers*.

    The two ratios get bootstrap intervals; *rng_for* gives the generator for a metric name.
    """
    checks = [a.citation_check for a in answers]
    sentences = sum(c.sentences for c in checks)
    dropped = [d.reason for c in checks for d in c.dropped]
    citations = sum(c.citations for c in checks)
    retrieved = sum(c.citations_retrieved for c in checks)
    return {
        "status": dict(Counter(a.status for a in answers)),
        "sentences": sentences,
        "dropped": len(dropped),
        "dropped_share": len(dropped) / sentences if sentences else None,
        "dropped_by_reason": dict(Counter(dropped)),
        "citations": citations,
        "citations_retrieved": retrieved,
        "structural_citation_validity": retrieved / citations if citations else None,
        "refused": sum(a.status == "refused" for a in answers),
        "truncated": sum(a.stop_reason == "max_tokens" for a in answers),
        "replayed_from_cache": sum(a.from_cache for a in answers),
        "intervals": {
            "dropped_share": ratio_interval(
                [(len(c.dropped), c.sentences) for c in checks], rng_for("dropped_share")),
            "structural_citation_validity": ratio_interval(
                [(c.citations_retrieved, c.citations) for c in checks],
                rng_for("structural_citation_validity")),
        },
    }
