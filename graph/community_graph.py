"""The community graph (Step 12): what Leiden finds communities on.

community_graph() is pure. It reads the graph builder's batch, the same nodes
and edges `graph.load` writes, and keeps:

- extracted nodes other than MetricValue, keyed by their `key`. Company,
  Filing, Period and Chunk nodes are left out, so an edge with a filer end
  does not count;
- extracted edges between two kept nodes, weighted by their evidence chunk
  count (`len(chunk_ids)`), in either direction;
- co-occurrence edges, weighted by the number of chunks two kept nodes share
  as node evidence. They exist only here and are never written to the graph.

Both weights are summed into one undirected weight per pair, stored as
(smaller key, larger key). Self-loops are dropped. Nodes with no edge are
returned as singletons, outside the graph.

`input_sha256` covers what the graph is made from: each kept node's label,
key and evidence chunk IDs, and each kept extracted edge's type, ends and
chunk IDs. A swapped evidence chunk changes it even when no weight changes,
so a communities file can be matched to the builder output it came from.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import combinations
from types import MappingProxyType

from graph.batch import CHUNK_IDS, Batch
from graph.ontology import EDGE_TYPES_BY_NAME, LABELS_BY_NAME, Kind

METRIC = LABELS_BY_NAME["MetricValue"].name
KEY = "key"

Pair = tuple[str, str]


class DuplicateKey(ValueError):
    """Two kept nodes share a key, so a key cannot name one community member."""


@dataclass(frozen=True, slots=True)
class CommunityGraph:
    labels: Mapping[str, str]  # key -> label, for every node with an edge
    extracted: Mapping[Pair, int]  # evidence chunks of extracted edges, per pair
    co_occurrence: Mapping[Pair, int]  # shared evidence chunks, per pair
    singletons: tuple[str, ...]  # sorted keys of kept nodes with no edge
    input_sha256: str  # the kept nodes' evidence chunks and the kept edges' chunk IDs
    weights: Mapping[Pair, int] = field(init=False)

    def __post_init__(self) -> None:
        summed = Counter(self.extracted)
        summed.update(self.co_occurrence)
        object.__setattr__(self, "weights", MappingProxyType(dict(sorted(summed.items()))))


def _kept(label: str) -> bool:
    return LABELS_BY_NAME[label].kind is Kind.EXTRACTED and label != METRIC


def _pair(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


def community_graph(batch: Batch) -> CommunityGraph:
    """The community graph of *batch*. DuplicateKey when two kept nodes share a key."""
    labels: dict[str, str] = {}
    hashed: list[list[object]] = []
    by_chunk: defaultdict[str, set[str]] = defaultdict(set)
    for node in batch.nodes:
        if not _kept(node.label):
            continue
        key = str(node.properties[KEY])
        if key in labels:
            raise DuplicateKey(f"two community graph nodes share the key {key!r}")
        labels[key] = node.label
        hashed.append(["node", node.label, key, sorted(e.chunk_id for e in node.evidence)])
        for evidence in node.evidence:
            by_chunk[evidence.chunk_id].add(key)

    extracted: Counter[Pair] = Counter()
    for edge in batch.edges:
        if EDGE_TYPES_BY_NAME[edge.type].kind is not Kind.EXTRACTED:
            continue
        if not (_kept(edge.start.label) and _kept(edge.end.label)):
            continue
        start, end = (str(ref.key_dict()[KEY]) for ref in (edge.start, edge.end))
        chunk_ids = sorted(edge.properties[CHUNK_IDS])  # type: ignore[call-overload]
        hashed.append(["edge", edge.type, start, end, chunk_ids])
        if start != end:
            extracted[_pair(start, end)] += len(chunk_ids)

    co_occurrence: Counter[Pair] = Counter()
    for keys in by_chunk.values():
        co_occurrence.update(combinations(sorted(keys), 2))

    linked = {key for pair in (*extracted, *co_occurrence) for key in pair}
    return CommunityGraph(
        labels=MappingProxyType({k: labels[k] for k in sorted(linked)}),
        extracted=MappingProxyType(dict(sorted(extracted.items()))),
        co_occurrence=MappingProxyType(dict(sorted(co_occurrence.items()))),
        singletons=tuple(sorted(labels.keys() - linked)),
        input_sha256=_sha256(sorted(hashed, key=_canonical)),
    )


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()
