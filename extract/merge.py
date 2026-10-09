"""Merging one filing's checked chunk outputs into write-path records (Step 8).

Nodes merge on their key and edges on (type, start, end, role), as the write
path merges them (ADR-0004). Each record keeps one entry per chunk, in chunk
order; when one chunk gives the same item twice, its more confident entry is
kept, ties to the later one, and the other is dropped before properties are
compared, so conflicts are always between chunks. A single-valued property that differs
takes the value from the most confident evidence (stated > implied >
uncertain), ties to the later chunk, and each disagreement is a conflict
holding both values.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from extract.check import CheckedEdge, CheckedNode, ChunkCheck
from extract.outcomes import Conflict
from graph.batch import (
    CHUNK_IDS,
    CONFIDENCES,
    EVIDENCE_SPANS,
    EXTRACT_PROMPTS,
    EdgeRecord,
    Evidence,
    NodeRecord,
)
from graph.ontology import CONFIDENCE_LEVELS

_Item = TypeVar("_Item", CheckedNode, CheckedEdge)
# stated outranks implied outranks uncertain
_RANK = {level: len(CONFIDENCE_LEVELS) - i for i, level in enumerate(CONFIDENCE_LEVELS)}


@dataclass(frozen=True, slots=True)
class Merged:
    nodes: tuple[NodeRecord, ...]
    edges: tuple[EdgeRecord, ...]
    conflicts: tuple[Conflict, ...]


@dataclass(frozen=True, slots=True)
class _Value:
    value: object
    rank: int
    chunk_id: str


def merge(checks: Sequence[ChunkCheck]) -> Merged:
    """One record per node and edge across *checks*, given in chunk order."""
    node_groups: dict[object, list[CheckedNode]] = {}
    edge_groups: dict[object, list[CheckedEdge]] = {}
    for check in checks:
        for n in check.nodes:
            node_groups.setdefault(n.ref, []).append(n)
        for e in check.edges:
            edge_groups.setdefault((e.type, e.start, e.end, e.role), []).append(e)
    nodes = [_node_record(_best_per_chunk(g)) for g in node_groups.values()]
    edges = [_edge_record(_best_per_chunk(g)) for g in edge_groups.values()]
    return Merged(
        nodes=tuple(record for record, _ in nodes),
        edges=tuple(record for record, _ in edges),
        conflicts=tuple(c for _, found in (*nodes, *edges) for c in found),
    )


def _best_per_chunk(group: Iterable[_Item]) -> tuple[_Item, ...]:
    """One entry per chunk, in order of first appearance: the most confident, ties to later."""
    best: dict[str, _Item] = {}
    for item in group:
        kept = best.get(item.evidence.chunk_id)
        if kept is None or _RANK[item.evidence.confidence] >= _RANK[kept.evidence.confidence]:
            best[item.evidence.chunk_id] = item
    return tuple(best.values())


def _resolve(
    item: str, entries: Iterable[tuple[Mapping[str, object], Evidence]],
) -> tuple[dict[str, object], tuple[Conflict, ...]]:
    """Each property's winning value across chunks, and every disagreement found."""
    winners: dict[str, _Value] = {}
    conflicts: list[Conflict] = []
    for properties, evidence in entries:
        rank = _RANK[evidence.confidence]
        for name, value in properties.items():
            current = winners.get(name)
            new = _Value(value, rank, evidence.chunk_id)
            if current is None:
                winners[name] = new
                continue
            if value == current.value:
                continue
            kept, other = (new, current) if rank >= current.rank else (current, new)
            conflicts.append(Conflict(item, name, kept.value, other.value,
                                      kept.chunk_id, other.chunk_id))
            winners[name] = kept
    return {name: v.value for name, v in winners.items()}, tuple(conflicts)


def _node_record(group: Sequence[CheckedNode]) -> tuple[NodeRecord, tuple[Conflict, ...]]:
    first = group[0]
    properties, conflicts = _resolve(str(first.ref), ((n.properties, n.evidence) for n in group))
    return NodeRecord(first.label, properties, tuple(n.evidence for n in group)), conflicts


def _edge_record(group: Sequence[CheckedEdge]) -> tuple[EdgeRecord, tuple[Conflict, ...]]:
    first = group[0]
    stakes = (({"stake": e.stake} if e.stake is not None else {}, e.evidence) for e in group)
    properties, conflicts = _resolve(first.item(), stakes)
    evidence = [e.evidence for e in group]
    return EdgeRecord(first.type, first.start, first.end, {
        **({"role": first.role} if first.role is not None else {}),
        **properties,
        CHUNK_IDS: tuple(e.chunk_id for e in evidence),
        EVIDENCE_SPANS: tuple(e.span for e in evidence),
        CONFIDENCES: tuple(e.confidence for e in evidence),
        EXTRACT_PROMPTS: tuple(e.extract_prompt for e in evidence),
    }), conflicts
