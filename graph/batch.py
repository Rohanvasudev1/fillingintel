"""Batches of graph writes and the pure check they must pass (Step 7).

A batch holds nodes and edges as plain typed records. check_batch() returns
every way the batch breaks the ontology or the evidence rules (ADR-0004),
with no database call: the caller says which chunks and endpoint nodes are
already in the graph.
"""
from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType

from graph.ontology import EDGE_TYPES_BY_NAME, LABELS_BY_NAME, Kind, Prop, PropType

# Set by the write path on every node and edge, never by the caller.
RESERVED = "ontology_version"
_EVIDENCED_BY = "EVIDENCED_BY"
_CHUNK = "Chunk"


class Rule(StrEnum):
    """The rule a violation breaks."""

    UNKNOWN_LABEL = "unknown_label"
    UNKNOWN_EDGE_TYPE = "unknown_edge_type"
    DISALLOWED_ENDPOINT = "disallowed_endpoint"
    UNKNOWN_ENDPOINT = "unknown_endpoint"
    BAD_REFERENCE = "bad_reference"
    MISSING_PROPERTY = "missing_property"
    WRONG_TYPE = "wrong_type"
    UNKNOWN_PROPERTY = "unknown_property"
    RESERVED_PROPERTY = "reserved_property"
    MISSING_EVIDENCE = "missing_evidence"
    UNEXPECTED_EVIDENCE = "unexpected_evidence"
    EVIDENCE_LENGTH_MISMATCH = "evidence_length_mismatch"
    DUPLICATE_CHUNK = "duplicate_chunk"
    UNKNOWN_CHUNK = "unknown_chunk"


def _frozen(mapping: Mapping[str, object]) -> MappingProxyType[str, object]:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True, slots=True)
class NodeRef:
    """A node named by its label and key properties, as an edge endpoint."""

    label: str
    key: tuple[tuple[str, object], ...]

    @classmethod
    def of(cls, label: str, **key: object) -> NodeRef:
        return cls(label, tuple(sorted(key.items())))

    def key_dict(self) -> dict[str, object]:
        return dict(self.key)

    def __str__(self) -> str:
        return f"{self.label}({', '.join(f'{k}={v!r}' for k, v in self.key)})"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One chunk an extracted node was read from, with the quote that names it."""

    chunk_id: str
    span: str


@dataclass(frozen=True, slots=True)
class NodeRecord:
    """A node to merge on its key properties; extracted nodes carry their evidence."""

    label: str
    properties: Mapping[str, object]
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "properties", _frozen(self.properties))


@dataclass(frozen=True, slots=True)
class EdgeRecord:
    """An edge between two nodes named by their keys."""

    type: str
    start: NodeRef
    end: NodeRef
    properties: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "properties", _frozen(self.properties))


@dataclass(frozen=True, slots=True)
class Batch:
    """Nodes and edges written together, or not at all."""

    nodes: tuple[NodeRecord, ...] = ()
    edges: tuple[EdgeRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class Violation:
    """One broken rule: which item, which rule, and a readable message."""

    item: str
    rule: Rule
    message: str

    def __str__(self) -> str:
        return f"{self.item}: {self.rule}: {self.message}"


def check_batch(
    batch: Batch,
    existing_chunk_ids: frozenset[str] = frozenset(),
    existing_nodes: frozenset[NodeRef] = frozenset(),
) -> tuple[Violation, ...]:
    """Every violation in *batch*, in batch order; empty when it may be written.

    A chunk an item cites must be a Chunk node in the batch or in
    *existing_chunk_ids*; an edge endpoint must be a node in the batch or in
    *existing_nodes*.
    """
    known_chunks = existing_chunk_ids | {
        n.properties.get("chunk_id") for n in batch.nodes if n.label == _CHUNK
    }
    known_nodes = existing_nodes | {
        ref for ref in (node_ref(n) for n in batch.nodes) if ref is not None
    }
    return (
        *(v for i, n in enumerate(batch.nodes) for v in _node_violations(i, n, known_chunks)),
        *(
            v for i, e in enumerate(batch.edges)
            for v in _edge_violations(i, e, known_chunks, known_nodes)
        ),
    )


def node_ref(node: NodeRecord) -> NodeRef | None:
    """The reference that names *node*, or None when its label or key is invalid."""
    definition = LABELS_BY_NAME.get(node.label)
    if definition is None:
        return None
    key = {k: node.properties.get(k) for k in definition.keys}
    if not all(_is_text(v) for v in key.values()):
        return None
    return NodeRef.of(node.label, **key)


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_type(value: object, prop_type: PropType) -> bool:
    if isinstance(value, bool):
        return False
    match prop_type:
        case PropType.STRING:
            return isinstance(value, str)
        case PropType.INTEGER:
            return isinstance(value, int)
        case PropType.FLOAT:
            return isinstance(value, int | float) and math.isfinite(value)
        case PropType.DATE:
            return isinstance(value, date) and not isinstance(value, datetime)
        case PropType.LIST_STRING:
            return isinstance(value, list | tuple) and all(isinstance(v, str) for v in value)


def _property_violations(
    item: str, props: tuple[Prop, ...], values: Mapping[str, object],
) -> Iterator[Violation]:
    defined = {p.name: p for p in props}
    for name in values:
        if name == RESERVED:
            yield Violation(item, Rule.RESERVED_PROPERTY,
                            f"{RESERVED} is set by the write path, not the caller")
        elif name not in defined:
            yield Violation(item, Rule.UNKNOWN_PROPERTY, f"{name!r} is not in the ontology")
    for prop in props:
        if prop.name == RESERVED:
            continue
        value = values.get(prop.name)
        if value is None or (prop.type is PropType.STRING and not _is_text(value)):
            if prop.required:
                yield Violation(item, Rule.MISSING_PROPERTY, f"{prop.name} is required")
        elif not _has_type(value, prop.type):
            yield Violation(item, Rule.WRONG_TYPE,
                            f"{prop.name} must be {prop.type}, got {type(value).__name__}")


def _node_violations(
    index: int, node: NodeRecord, known_chunks: frozenset[object],
) -> Iterator[Violation]:
    definition = LABELS_BY_NAME.get(node.label)
    item = f"node {index} {node_ref(node) or repr(node.label)}"
    if definition is None:
        yield Violation(item, Rule.UNKNOWN_LABEL, f"{node.label!r} is not an ontology label")
        return
    yield from _property_violations(item, definition.properties, node.properties)
    if definition.kind is Kind.STRUCTURAL:
        if node.evidence:
            yield Violation(item, Rule.UNEXPECTED_EVIDENCE,
                            f"{node.label} is structural; its evidence is the filing record")
        return
    if not node.evidence:
        yield Violation(item, Rule.MISSING_EVIDENCE, "an extracted node needs evidence")
    yield from _evidence_violations(
        item, [e.chunk_id for e in node.evidence], [e.span for e in node.evidence], known_chunks,
    )


def _evidence_violations(
    item: str, chunk_ids: list[object], spans: list[object], known_chunks: frozenset[object],
) -> Iterator[Violation]:
    for span in spans:
        if not _is_text(span):
            yield Violation(item, Rule.MISSING_EVIDENCE, "an evidence span is blank")
    seen: set[object] = set()
    for chunk_id in chunk_ids:
        if chunk_id in seen:
            yield Violation(item, Rule.DUPLICATE_CHUNK, f"chunk {chunk_id!r} is listed twice")
        elif chunk_id not in known_chunks:
            yield Violation(item, Rule.UNKNOWN_CHUNK,
                            f"chunk {chunk_id!r} is neither in the batch nor in the graph")
        if isinstance(chunk_id, str):
            seen.add(chunk_id)


def _endpoint_violations(
    item: str, end: str, ref: NodeRef, allowed: tuple[str, ...], known_nodes: frozenset[NodeRef],
) -> Iterator[Violation]:
    if ref.label not in allowed:
        yield Violation(item, Rule.DISALLOWED_ENDPOINT,
                        f"{end} label {ref.label!r} is not one of {', '.join(allowed)}")
        return
    keys = LABELS_BY_NAME[ref.label].keys
    if sorted(k for k, _ in ref.key) != sorted(keys) or not all(_is_text(v) for _, v in ref.key):
        yield Violation(item, Rule.BAD_REFERENCE,
                        f"{end} must name {ref.label} by {', '.join(keys)} as text")
    elif ref not in known_nodes:
        yield Violation(item, Rule.UNKNOWN_ENDPOINT,
                        f"{end} {ref} is neither in the batch nor in the graph")


def _edge_violations(
    index: int, edge: EdgeRecord, known_chunks: frozenset[object],
    known_nodes: frozenset[NodeRef],
) -> Iterator[Violation]:
    item = f"edge {index} {edge.start}-[{edge.type}]->{edge.end}"
    definition = EDGE_TYPES_BY_NAME.get(edge.type)
    if definition is None:
        yield Violation(item, Rule.UNKNOWN_EDGE_TYPE, f"{edge.type!r} is not an ontology edge type")
        return
    if edge.type == _EVIDENCED_BY:
        yield Violation(item, Rule.UNEXPECTED_EVIDENCE,
                        "node evidence goes on the node record, not as an edge")
        return
    yield from _endpoint_violations(item, "start", edge.start, definition.starts, known_nodes)
    yield from _endpoint_violations(item, "end", edge.end, definition.ends, known_nodes)
    yield from _property_violations(item, definition.properties, edge.properties)
    if definition.kind is Kind.EXTRACTED:
        yield from _edge_evidence_violations(item, edge.properties, known_chunks)


def _edge_evidence_violations(
    item: str, props: Mapping[str, object], known_chunks: frozenset[object],
) -> Iterator[Violation]:
    chunk_ids, spans = props.get("chunk_ids"), props.get("evidence_spans")
    if not isinstance(chunk_ids, list | tuple) or not isinstance(spans, list | tuple):
        return  # reported as a missing or wrongly typed property
    if not chunk_ids:
        yield Violation(item, Rule.MISSING_EVIDENCE, "an extracted edge needs chunk_ids")
    elif len(chunk_ids) != len(spans):
        yield Violation(item, Rule.EVIDENCE_LENGTH_MISMATCH,
                        f"{len(chunk_ids)} chunk_ids but {len(spans)} evidence_spans")
    yield from _evidence_violations(item, list(chunk_ids), list(spans), known_chunks)
