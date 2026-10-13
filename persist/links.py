"""Persistence links: selection and evidence (Step 10).

A later risk factor has at most one predecessor: its exact-title match, else
the highest-ranked `same` decision. An earlier factor may have several
successors, so a factor the filer split links both halves.

Each link is one PERSISTS_AS edge from the earlier factor to the later one,
with two evidence entries, earlier first: one chunk and span from each factor
(each factor's own first span for an exact-title link, the adjudicator's
verified quotes otherwise), confidence `stated` for both, and the method
(`persist/exact_title` or the prompt version) as the extract-prompt entry.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from graph.batch import (
    CHUNK_IDS,
    CONFIDENCES,
    EVIDENCE_SPANS,
    EXTRACT_PROMPTS,
    Batch,
    EdgeRecord,
    NodeRef,
    Violation,
    check_batch,
)
from persist.decisions import LinkEvidence, PersistDecision
from resolve.nodes import RISK_LABEL, ResolveNode

EDGE_TYPE = "PERSISTS_AS"
EXACT_TITLE = "persist/exact_title"
CONFIDENCE = "stated"


@dataclass(frozen=True, slots=True)
class PersistLink:
    """The same risk in an earlier 10-K and a later filing."""

    earlier_key: str
    later_key: str
    method: str  # EXACT_TITLE or the adjudicator's prompt version
    earlier: LinkEvidence
    later: LinkEvidence
    decision_id: str | None = None  # None for an exact-title link
    rank: int | None = None

    @property
    def is_exact(self) -> bool:
        return self.method == EXACT_TITLE

    def edge(self) -> EdgeRecord:
        """The PERSISTS_AS edge, as the write path takes it."""
        return EdgeRecord(
            type=EDGE_TYPE,
            start=NodeRef.of(RISK_LABEL, key=self.earlier_key),
            end=NodeRef.of(RISK_LABEL, key=self.later_key),
            properties={
                CHUNK_IDS: [self.earlier.chunk_id, self.later.chunk_id],
                EVIDENCE_SPANS: [self.earlier.span, self.later.span],
                CONFIDENCES: [CONFIDENCE, CONFIDENCE],
                EXTRACT_PROMPTS: [self.method, self.method],
            },
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "earlier_key": self.earlier_key, "later_key": self.later_key, "method": self.method,
            "earlier": {"chunk_id": self.earlier.chunk_id, "span": self.earlier.span},
            "later": {"chunk_id": self.later.chunk_id, "span": self.later.span},
            "decision_id": self.decision_id, "rank": self.rank,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> PersistLink:
        """A link from its file form; ValueError when it is not one."""
        try:
            link = cls(
                earlier_key=_text(data["earlier_key"]), later_key=_text(data["later_key"]),
                method=_text(data["method"]), earlier=_evidence(data["earlier"]),
                later=_evidence(data["later"]),
                decision_id=None if data["decision_id"] is None else _text(data["decision_id"]),
                rank=_rank(data["rank"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"not a persistence link: {exc}") from exc
        if link.is_exact != (link.decision_id is None):
            raise ValueError("a persistence link has a decision exactly when it is adjudicated")
        return link


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{value!r} is not a non-empty string")
    return value


def _evidence(value: object) -> LinkEvidence:
    if not isinstance(value, Mapping):
        raise TypeError("evidence is not an object")
    return LinkEvidence(_text(value["chunk_id"]), _text(value["span"]))


def _rank(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TypeError(f"rank {value!r} is not a whole number of at least 1")
    return value


def exact_link(later: ResolveNode, earlier: ResolveNode) -> PersistLink:
    """The link between two factors with the same normalized title."""
    return PersistLink(
        earlier_key=earlier.key, later_key=later.key, method=EXACT_TITLE,
        earlier=LinkEvidence(earlier.evidence[0].chunk_id, earlier.evidence[0].span),
        later=LinkEvidence(later.evidence[0].chunk_id, later.evidence[0].span),
    )


def decision_link(decision: PersistDecision) -> PersistLink:
    """The link a verified `same` decision makes; ValueError for any other decision."""
    if not decision.links or decision.verified_earlier is None \
            or decision.verified_later is None:
        raise ValueError(f"decision {decision.decision_id} does not link")
    return PersistLink(
        earlier_key=decision.earlier_key, later_key=decision.later_key,
        method=decision.prompt_version, earlier=decision.verified_earlier,
        later=decision.verified_later,
        decision_id=decision.decision_id, rank=decision.rank,
    )


def select_links(exact: Sequence[PersistLink], decisions: Sequence[PersistDecision]
                 ) -> tuple[PersistLink, ...]:
    """At most one predecessor per later factor, sorted by later key."""
    chosen = {link.later_key: link for link in exact}
    best: dict[str, PersistDecision] = {}
    for d in decisions:
        if d.links and d.later_key not in chosen and (
                d.later_key not in best or d.rank < best[d.later_key].rank):
            best[d.later_key] = d
    chosen.update({key: decision_link(d) for key, d in best.items()})
    return tuple(chosen[key] for key in sorted(chosen))


def check_links(links: Sequence[PersistLink]) -> tuple[Violation, ...]:
    """check_batch() over every link's edge, as a dry run of the write path.

    The risk factors and chunks the links name stand in for the graph, so
    only the edges' own shape and evidence are checked here; ticket 07's load
    checks that the factors exist.
    """
    edges = tuple(link.edge() for link in links)
    chunks = frozenset(c for link in links for c in (link.earlier.chunk_id, link.later.chunk_id))
    nodes = frozenset(ref for e in edges for ref in (e.start, e.end))
    return check_batch(Batch(edges=edges), existing_chunk_ids=chunks, existing_nodes=nodes)
