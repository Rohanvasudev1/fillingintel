"""Risk factors around real titles and chunk IDs from the AMD fixture filings (Step 10).

The later filing is the AMD FY2026-Q2 10-Q fixture, whose most recent 10-K is the
AMD FY2025 10-K fixture. Earlier titles are as AMD run 1 extracted them; later
titles are the 10-Q's bold headings. Each factor's span is its title as the
chunk prints it, so spans pass the Step 8 check against the fixture text.
"""
from datetime import date

from persist.decisions import Answer, PersistDecision
from persist.filings import Filing
from resolve.nodes import EvidenceRef, ResolveNode

AMD_10K = "0000002488-26-000018"
AMD_10Q = "0000002488-26-000123"
FILINGS = (
    Filing(AMD_10K, "2488", "10-K", date(2025, 12, 27)),
    Filing(AMD_10Q, "2488", "10-Q", date(2026, 6, 27)),
)
SEASONAL = "Our operating results are subject to quarterly and seasonal sales patterns"
GUARANTEES_10K = "We may be required to satisfy financial obligations under guarantees and other " \
                 "commercial commitments."
GUARANTEES_10Q = "We may be required to satisfy financial obligations under guarantees, leases " \
                 "and other commercial commitments."
CURRENCY = "Unfavorable currency exchange rate fluctuations could adversely affect us"
CLIMATE = "Supply chain delays resulting from climate change related disruptions may lead to " \
          "contractual disputes, litigation and increasing costs"
LITIGATION_10Q = ("We are party to litigation and may become a party to other claims or "
                  "litigation that could cause us to incur substantial costs or pay substantial "
                  "damages or prohibit us from selling our products.")


def factor(title: str, accession_no: str, *ordinals: int, span: str | None = None
           ) -> ResolveNode:
    """A RiskFactor keyed as extraction keys it, with one evidence entry per ordinal."""
    return ResolveNode(
        key=f"{accession_no}:{' '.join(title.casefold().split())}", label="RiskFactor",
        name=title,
        evidence=tuple(EvidenceRef(f"{accession_no}:{o:04d}", span or title) for o in ordinals),
    )


def earlier(title: str, *ordinals: int, span: str | None = None) -> ResolveNode:
    return factor(title, AMD_10K, *ordinals, span=span)


def later(title: str, *ordinals: int, span: str | None = None) -> ResolveNode:
    return factor(title, AMD_10Q, *ordinals, span=span)


PROMPT_VERSION = "persist/v1@0123abcd"


class ScriptedAdjudicator:
    """Answers each (later title, earlier title) from *answers*, `distinct` otherwise; a
    `same` quotes each factor's first span from its first chunk."""

    prompt_version = PROMPT_VERSION

    def __init__(self, answers=None, verified=True):
        self._answers = dict(answers or {})
        self._verified = verified
        self.judged = []

    def judge(self, candidate, later_factor, earlier_factor):
        self.judged.append((later_factor.name, earlier_factor.name))
        answer = self._answers.get((later_factor.name, earlier_factor.name), Answer.DISTINCT)
        same = answer is Answer.SAME
        failure = None if same and self._verified else (
            "quote_not_found" if same else answer.value)
        e, lt = earlier_factor.evidence[0], later_factor.evidence[0]
        return PersistDecision(
            decision_id=f"d-{later_factor.name}-{earlier_factor.name}",
            later_key=candidate.later_key, earlier_key=candidate.earlier_key, rank=candidate.rank,
            answer=answer, quote_earlier=e.span, chunk_id_earlier=e.chunk_id,
            quote_later=lt.span, chunk_id_later=lt.chunk_id,
            span_earlier=e.span if failure is None else None,
            span_later=lt.span if failure is None else None,
            reason="scripted", failure=failure, prompt_version=self.prompt_version,
        )


LITIGATION_10K = LITIGATION_10Q.removesuffix(".")
# Real AMD pairs whose replies scripts/capture_persist_responses.py records: fixture name ->
# (later factor, earlier factor).
RECORDED_PAIRS = {
    "seasonal_period": (later(SEASONAL + ".", 49), earlier(SEASONAL, 21, 27)),
    "guarantees_leases": (later(GUARANTEES_10Q, 78), earlier(GUARANTEES_10K, 53)),
    "litigation": (later(LITIGATION_10Q, 71), earlier(LITIGATION_10K, 47)),
    "litigation_climate": (later(LITIGATION_10Q, 71), earlier(CLIMATE, 41)),
    "currency_seasonal": (later(CURRENCY + ".", 50), earlier(SEASONAL, 21, 27)),
}
