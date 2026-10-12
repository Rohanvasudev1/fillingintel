"""The adjudicator's per-pair decision record and the adjudicator seam (Step 9).

Ticket 02 fixes these shapes; ticket 03 fills them from claude-sonnet-5-5
replies. A decision merges only when the answer is `same` and both quotes were
found in their own node's chunks.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from resolve.candidates import ScoredPair
from resolve.nodes import ResolveNode
from retrieve.answer_model import TokenUsage


class Answer(StrEnum):
    SAME = "same"
    RELATED = "related"
    DISTINCT = "distinct"
    UNSURE = "unsure"


class RelatedKind(StrEnum):
    FAMILY_MEMBER = "family_member"
    PARENT_SUBSIDIARY = "parent_subsidiary"
    VARIANT = "variant"


class Failure(StrEnum):
    """Why a decision does not merge. A `related` answer counts as `related:{kind}`."""

    CALL_FAILED = "call_failed"  # the API call raised; a rerun tries again
    INVALID_REPLY = "invalid_reply"  # cut off, refused, not JSON or not the schema's shape
    QUOTE_NOT_FOUND = "quote_not_found"
    QUOTE_FROM_OTHER_NODE = "quote_from_other_node"
    UNSURE = "unsure"
    DISTINCT = "distinct"


def related_failure(kind: RelatedKind) -> str:
    return f"related:{kind.value}"


@dataclass(frozen=True, slots=True)
class PairDecision:
    """One adjudicated candidate pair (key_a < key_b, as in ScoredPair)."""

    decision_id: str
    key_a: str
    key_b: str
    label: str
    answer: Answer | None  # None when no usable reply came back
    related_kind: RelatedKind | None
    quote_a: str | None
    chunk_id_a: str | None
    quote_b: str | None
    chunk_id_b: str | None
    reason: str
    quotes_verified: bool
    failure: str | None  # why the decision cannot merge, counted by reason in the report
    prompt_version: str
    usage: TokenUsage | None = None  # None when no reply came back
    from_cache: bool = False

    @property
    def merges(self) -> bool:
        return self.answer is Answer.SAME and self.quotes_verified and self.failure is None


class Adjudicator(Protocol):
    """Judges one candidate pair from both nodes' names, evidence and chunk text."""

    prompt_version: str

    def judge(self, pair: ScoredPair, a: ResolveNode, b: ResolveNode) -> PairDecision: ...


@dataclass(frozen=True, slots=True)
class HeldBackGroup:
    """A group or piece of `same` decisions not merged, with the reason (Step 9b: too
    many names, too many parts to search, or a contradiction no cut separates)."""

    keys: tuple[str, ...]
    reason: str
    decisions: tuple[PairDecision, ...]


@dataclass(frozen=True, slots=True)
class CutGroup:
    """A group cut into pieces because it held a `related` or `distinct` pair (Step 9b)."""

    keys: tuple[str, ...]  # the whole group, sorted
    pieces: tuple[tuple[str, ...], ...]  # every piece, single keys included, by smallest key
    cut: tuple[PairDecision, ...]  # the merging `same` decisions between pieces, by ID
    tied: tuple[str, ...]  # keys left out of a piece because tied cuts placed them differently
    smallest_cuts: int  # how many cuts shared the smallest size
