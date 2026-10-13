"""The persistence adjudicator's per-pair decision and the adjudicator seam (Step 10).

A decision links only when the answer is `same` and both quotes were found in
their own risk factor's chunks; each verified quote is then held as the
chunk's own text, as Step 8 stores spans.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from persist.candidates import CandidatePair
from resolve.nodes import ResolveNode
from retrieve.answer_model import TokenUsage


class Answer(StrEnum):
    SAME = "same"
    DISTINCT = "distinct"
    UNSURE = "unsure"


class Failure(StrEnum):
    """Why a decision does not link."""

    CALL_FAILED = "call_failed"  # the API call raised; a rerun tries again
    INVALID_REPLY = "invalid_reply"  # cut off, refused, not JSON or not the schema's shape
    QUOTE_NOT_FOUND = "quote_not_found"
    QUOTE_FROM_OTHER_FACTOR = "quote_from_other_factor"
    UNSURE = "unsure"
    DISTINCT = "distinct"


@dataclass(frozen=True, slots=True)
class LinkEvidence:
    """One evidence entry of a persistence link: a chunk and the span it holds."""

    chunk_id: str
    span: str


@dataclass(frozen=True, slots=True)
class PersistDecision:
    """One adjudicated candidate pair: a later risk factor and an earlier one."""

    decision_id: str
    later_key: str
    earlier_key: str
    rank: int  # the earlier factor's place among the later one's neighbours
    answer: Answer | None  # None when no usable reply came back
    quote_earlier: str | None
    chunk_id_earlier: str | None
    quote_later: str | None
    chunk_id_later: str | None
    verified_earlier: LinkEvidence | None  # the quote as the chunk's own text, once verified
    verified_later: LinkEvidence | None
    reason: str
    failure: Failure | None  # why the decision cannot link, counted by reason in the report
    prompt_version: str
    usage: TokenUsage | None = None  # None when no reply came back
    from_cache: bool = False

    @property
    def links(self) -> bool:
        return (self.answer is Answer.SAME and self.failure is None
                and self.verified_earlier is not None and self.verified_later is not None)


class PersistAdjudicator(Protocol):
    """Judges whether an earlier risk factor is the same risk as a later one."""

    prompt_version: str

    def judge(self, candidate: CandidatePair, later: ResolveNode,
              earlier: ResolveNode) -> PersistDecision: ...
