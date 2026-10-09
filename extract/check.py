"""Checking one chunk's reply: from candidates to checked nodes and edges (Step 8).

Code does what the model must not. For each candidate it checks the label or
edge type against the ontology, maps filer references, resolves local node
IDs, checks endpoint pairs, required properties and their types, and finds the
evidence span in the chunk's text. Then it builds node keys. A candidate that
breaks a rule is rejected with one reason; a rejected node takes every triple
touching it with it. Nothing here calls a model or touches a file.

Checks run in this order, and the first failure is the reason given:
nodes: label, id, confidence, properties, span; then the key, which for a
MetricValue needs exactly one MEASURES subject in the same reply; triples:
endpoints, edge type, endpoint pair, confidence, properties, span.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from extract import fields
from extract.fields import Rejected
from extract.filers import FilingInfo, resolve_filer
from extract.keys import metric_key, node_key
from extract.outcomes import CandidateKind, Flag, FlagReason, Reason, Rejection
from extract.output_schema import CROSS_FILING_EDGE_TYPES
from extract.request import ChunkInput
from extract.spans import find_span, normalize
from graph.batch import Evidence, NodeRef
from graph.ontology import EDGE_TYPES_BY_NAME, LABELS_BY_NAME, Confidence, EdgeDef, Kind

METRIC = "MetricValue"
MEASURES = "MEASURES"
_NAME_PROPERTY = {"RiskFactor": "title", METRIC: "concept"}
_FILER_LIKE = re.compile(r"[A-Z][A-Z_]*")  # an unknown reference written like THIS_FILING


class ReplyShapeError(ValueError):
    """The reply is not an object with a list of nodes and a list of triples."""


@dataclass(frozen=True, slots=True)
class CheckedNode:
    ref: NodeRef
    label: str
    properties: Mapping[str, object]  # key included
    evidence: Evidence

    def __post_init__(self) -> None:
        object.__setattr__(self, "properties", MappingProxyType(dict(self.properties)))


@dataclass(frozen=True, slots=True)
class CheckedEdge:
    type: str
    start: NodeRef
    end: NodeRef
    role: str | None
    stake: float | None
    evidence: Evidence

    def item(self) -> str:
        return f"{self.start}-[{self.type}]->{self.end}"


@dataclass(frozen=True, slots=True)
class ChunkCheck:
    """One chunk's checked output."""

    chunk_id: str
    nodes: tuple[CheckedNode, ...]
    edges: tuple[CheckedEdge, ...]
    rejections: tuple[Rejection, ...]
    flags: tuple[Flag, ...]
    candidates: int


@dataclass(frozen=True, slots=True)
class _Context:
    chunk: ChunkInput
    filing: FilingInfo
    prompt_version: str

    def reject(self, kind: CandidateKind, raw: object, exc: Rejected) -> Rejection:
        candidate = raw if isinstance(raw, Mapping) else {"candidate": raw}
        return Rejection(self.chunk.chunk_id, kind, exc.reason, exc.message, candidate)


@dataclass(frozen=True, slots=True)
class _Draft:
    """A node that passed its own checks and waits for its key."""

    local_id: str
    label: str
    name: str
    metric: Mapping[str, object]
    evidence: Evidence
    raw: Mapping[str, object]


def check_reply(
    reply: object, chunk: ChunkInput, filing: FilingInfo, prompt_version: str,
) -> ChunkCheck:
    """The checked nodes, edges, rejections and flags of one chunk's parsed reply."""
    if not (isinstance(reply, dict) and isinstance(reply.get("nodes"), list)
            and isinstance(reply.get("triples"), list)):
        raise ReplyShapeError("the reply is not an object with lists of nodes and triples")
    ctx = _Context(chunk, filing, prompt_version)
    raw_nodes: list[object] = reply["nodes"]
    raw_triples: list[object] = reply["triples"]
    drafts, rejections = _check_nodes(raw_nodes, ctx)
    nodes, key_rejections = _key_nodes(drafts, raw_triples, ctx)
    rejected_ids = set(_local_ids(raw_nodes)) - set(nodes)
    edges, triple_rejections = _check_triples(raw_triples, nodes, rejected_ids, ctx)
    return ChunkCheck(
        chunk_id=chunk.chunk_id,
        nodes=tuple(nodes.values()),
        edges=edges,
        rejections=(*rejections, *key_rejections, *triple_rejections),
        flags=(*_node_flags(nodes.values()), *_edge_flags(edges)),
        candidates=len(raw_nodes) + len(raw_triples),
    )


# ── nodes ─────────────────────────────────────────────────────────────────────

def _local_ids(raw_nodes: Sequence[object]) -> list[str]:
    """Every readable local ID in *raw_nodes*, repeats included."""
    return [i for i in (fields.optional_text(r, "id") for r in raw_nodes) if i is not None]


def _check_nodes(
    raw_nodes: Sequence[object], ctx: _Context,
) -> tuple[list[_Draft], list[Rejection]]:
    uses = Counter(_local_ids(raw_nodes))
    drafts: list[_Draft] = []
    rejections: list[Rejection] = []
    for raw in raw_nodes:
        try:
            draft = _check_node(raw, ctx)
            if uses[draft.local_id] > 1:
                raise Rejected(Reason.DANGLING_LOCAL_ID,
                               f"local ID {draft.local_id!r} is given to {uses[draft.local_id]} "
                               "nodes, so references to it are ambiguous")
            drafts.append(draft)
        except Rejected as exc:
            rejections.append(ctx.reject(CandidateKind.NODE, raw, exc))
    return drafts, rejections


def _check_node(raw: object, ctx: _Context) -> _Draft:
    if not isinstance(raw, dict):
        raise Rejected(Reason.WRONG_PROPERTY_TYPE, "a node must be an object")
    label = _label(raw)
    local_id = fields.text(raw, "id")
    confidence = fields.confidence(raw)
    name = fields.text(raw, "name")
    metric = _metric_properties(raw) if label == METRIC else {}
    return _Draft(local_id, label, name, metric, _evidence(raw, ctx, confidence), raw)


def _label(raw: Mapping[str, object]) -> str:
    given = fields.text(raw, "label")
    label = fields.lookup(given, LABELS_BY_NAME)
    if label is None:
        raise Rejected(Reason.UNKNOWN_LABEL, f"{given!r} is not an ontology label")
    if LABELS_BY_NAME[label].kind is Kind.STRUCTURAL:
        raise Rejected(Reason.STRUCTURAL_LABEL,
                       f"{label} nodes come from EDGAR metadata, not from the model")
    return label


def _metric_properties(raw: Mapping[str, object]) -> dict[str, object]:
    return {
        "value": fields.number(raw, "value", required=True),
        "unit": fields.text(raw, "unit"),
        "period": fields.text(raw, "period"),
    }


def _evidence(raw: Mapping[str, object], ctx: _Context, confidence: str) -> Evidence:
    span = fields.text(raw, "evidence_span")
    found = find_span(span, ctx.chunk.text)
    if found is None:
        raise Rejected(Reason.SPAN_NOT_FOUND, "the evidence span is not in the chunk's text")
    return Evidence(ctx.chunk.chunk_id, found, confidence, ctx.prompt_version)


def _key_nodes(
    drafts: Sequence[_Draft], raw_triples: Sequence[object], ctx: _Context,
) -> tuple[dict[str, CheckedNode], list[Rejection]]:
    """Checked nodes by local ID; a MetricValue without one subject is rejected."""
    accession_no = ctx.filing.accession_no
    nodes = {
        d.local_id: _checked(d, node_key(d.label, d.name, accession_no))
        for d in drafts if d.label != METRIC
    }
    rejections: list[Rejection] = []
    for draft in (d for d in drafts if d.label == METRIC):
        try:
            subject = _subject(draft.local_id, raw_triples, nodes, ctx)
            key = metric_key(accession_no, _subject_key(subject), draft.name,
                             str(draft.metric["period"]))
            nodes[draft.local_id] = _checked(draft, key)
        except Rejected as exc:
            rejections.append(ctx.reject(CandidateKind.NODE, draft.raw, exc))
    return nodes, rejections


def _checked(draft: _Draft, key: str) -> CheckedNode:
    properties = {"key": key, _NAME_PROPERTY.get(draft.label, "name"): draft.name, **draft.metric}
    return CheckedNode(NodeRef.of(draft.label, key=key), draft.label, properties, draft.evidence)


def _subject(
    metric_id: str, raw_triples: Sequence[object], nodes: Mapping[str, CheckedNode],
    ctx: _Context,
) -> NodeRef:
    """The one subject the reply's MEASURES triples give *metric_id*."""
    allowed = EDGE_TYPES_BY_NAME[MEASURES].ends
    ends = [
        fields.optional_text(raw, "end") for raw in raw_triples
        if (fields.optional_text(raw, "type") or "").casefold() == MEASURES.casefold()
        and fields.optional_text(raw, "start") == metric_id
    ]
    refs = {e: resolve_filer(e, ctx.filing) or (nodes[e].ref if e in nodes else None)
            for e in ends if e is not None}
    subjects = {ref for ref in refs.values() if ref is not None and ref.label in allowed}
    if len(subjects) == 1:
        return subjects.pop()
    if not subjects and any(ref is None for ref in refs.values()):
        raise Rejected(Reason.ENDPOINT_NODE_REJECTED,
                       "the MetricValue's MEASURES subject was rejected or names no node")
    raise Rejected(Reason.MISSING_PROPERTY,
                   f"a MetricValue needs one MEASURES subject ({', '.join(allowed)}); "
                   f"found {len(subjects)}")


def _subject_key(subject: NodeRef) -> str:
    key = subject.key_dict()
    return str(key["cik"] if subject.label == "Company" else key["key"])


# ── triples ───────────────────────────────────────────────────────────────────

def _check_triples(
    raw_triples: Sequence[object], nodes: Mapping[str, CheckedNode], rejected_ids: set[str],
    ctx: _Context,
) -> tuple[tuple[CheckedEdge, ...], list[Rejection]]:
    edges: list[CheckedEdge] = []
    rejections: list[Rejection] = []
    for raw in raw_triples:
        try:
            edges.append(_check_triple(raw, nodes, rejected_ids, ctx))
        except Rejected as exc:
            rejections.append(ctx.reject(CandidateKind.TRIPLE, raw, exc))
    return tuple(edges), rejections


def _check_triple(
    raw: object, nodes: Mapping[str, CheckedNode], rejected_ids: set[str], ctx: _Context,
) -> CheckedEdge:
    if not isinstance(raw, dict):
        raise Rejected(Reason.WRONG_PROPERTY_TYPE, "a triple must be an object")
    start = _endpoint(raw, "start", nodes, rejected_ids, ctx)
    end = _endpoint(raw, "end", nodes, rejected_ids, ctx)
    definition = _edge_type(raw)
    for side, ref, allowed in (("start", start, definition.starts), ("end", end, definition.ends)):
        if ref.label not in allowed:
            raise Rejected(Reason.DISALLOWED_ENDPOINT,
                           f"{definition.name} {side} {ref.label} is not one of "
                           f"{', '.join(allowed)}")
    confidence = fields.confidence(raw)
    names = {p.name for p in definition.properties}
    role = fields.text(raw, "role") if "role" in names else None
    stake = fields.number(raw, "stake", required=False) if "stake" in names else None
    evidence = _evidence(raw, ctx, confidence)
    if definition.symmetric and str(end) < str(start):
        start, end = end, start  # stored one way: the same pair merges whichever way it came
    return CheckedEdge(definition.name, start, end, role, stake, evidence)


def _endpoint(
    raw: Mapping[str, object], side: str, nodes: Mapping[str, CheckedNode],
    rejected_ids: set[str], ctx: _Context,
) -> NodeRef:
    reference = fields.text(raw, side)
    filer = resolve_filer(reference, ctx.filing)
    if filer is not None:
        return filer
    if reference in nodes:
        return nodes[reference].ref
    if reference in rejected_ids:
        raise Rejected(Reason.ENDPOINT_NODE_REJECTED, f"{side} node {reference!r} was rejected")
    if _FILER_LIKE.fullmatch(reference):
        raise Rejected(Reason.UNKNOWN_FILER_REFERENCE,
                       f"{side} {reference!r} is not a filer reference or a local node ID")
    raise Rejected(Reason.DANGLING_LOCAL_ID, f"{side} {reference!r} names no node in the reply")


def _edge_type(raw: Mapping[str, object]) -> EdgeDef:
    given = fields.text(raw, "type")
    name = fields.lookup(given, EDGE_TYPES_BY_NAME)
    if name is None:
        raise Rejected(Reason.UNKNOWN_EDGE_TYPE, f"{given!r} is not an ontology edge type")
    if name in CROSS_FILING_EDGE_TYPES:
        raise Rejected(Reason.UNKNOWN_EDGE_TYPE,
                       f"{name} links filings and is built in Step 10, not read from one chunk")
    definition = EDGE_TYPES_BY_NAME[name]
    if definition.kind is Kind.STRUCTURAL:
        raise Rejected(Reason.STRUCTURAL_LABEL,
                       f"{name} edges come from EDGAR metadata, not from the model")
    return definition


# ── flags ─────────────────────────────────────────────────────────────────────

def _node_flags(nodes: Iterable[CheckedNode]) -> list[Flag]:
    flags = []
    for n in nodes:
        name = str(n.properties[_NAME_PROPERTY.get(n.label, "name")])
        if normalize(name) not in normalize(n.evidence.span):
            flags.append(Flag(n.evidence.chunk_id, FlagReason.NAME_NOT_IN_SPAN, str(n.ref)))
        if n.evidence.confidence == Confidence.UNCERTAIN:
            flags.append(Flag(n.evidence.chunk_id, FlagReason.UNCERTAIN, str(n.ref)))
    return flags


def _edge_flags(edges: Sequence[CheckedEdge]) -> list[Flag]:
    return [Flag(e.evidence.chunk_id, FlagReason.UNCERTAIN, e.item())
            for e in edges if e.evidence.confidence == Confidence.UNCERTAIN]
