"""What a review round draws from: the candidates file and the seeded sampler (Step 8).

A round judges *size* edges. Each edge type gets a quota in proportion to its
count, with at least one per type present (seats after the first go by highest
averages, so a type never gets more seats than it has edges). The slots are
put in a seeded order, and each slot draws the next unused edge of its type in
a seeded order, or, when that type has none left, the next unused edge of any
type. A skipped edge is used, so its slot draws again. Each judgment records its
slot's type, so a slot that fell back to another type shows in the round file.
The draw depends only on the seed, the round number, the eligible edges and
which edges the round has used, so a resumed round draws exactly what it
would have drawn unbroken.

An edge read from several chunks is judged against one of them, chosen by a
seeded draw per edge.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from graph.batch import (
    CHUNK_IDS,
    CONFIDENCES,
    EVIDENCE_LISTS,
    EVIDENCE_SPANS,
    EXTRACT_PROMPTS,
    NodeRef,
)

NodeProperties = Mapping[str, object]
SEED = 8  # Step 8; rounds differ through the round number in the draw


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    chunk_id: str
    span: str
    confidence: str
    extract_prompt: str


@dataclass(frozen=True, slots=True)
class ReviewEdge:
    """One merged edge of a run: its endpoints as the candidates file names them."""

    edge_id: str  # the run's item name, plus the role when there is one
    item: str  # the run's item name, as flags name the edge
    type: str
    start: Mapping[str, object]
    end: Mapping[str, object]
    properties: Mapping[str, object]  # without the evidence lists
    evidence: tuple[EvidenceEntry, ...]
    start_item: str
    end_item: str


@dataclass(frozen=True, slots=True)
class Candidates:
    """A run's candidates file, read for review."""

    accession_no: str
    file_name: str
    sha256: str
    edges: tuple[ReviewEdge, ...]
    nodes: Mapping[str, NodeProperties]  # node item -> its properties
    flags: tuple[tuple[str, str, str], ...]  # (item, chunk_id, reason)


def _item(ref: Mapping[str, object]) -> str:
    """A node's name as the candidates file's items print it, e.g. Company(cik='1045810')."""
    key = ref["key"]
    if not isinstance(key, Mapping) or not isinstance(ref["label"], str):
        raise ValueError("an endpoint needs a label and a key object")
    return str(NodeRef.of(ref["label"], **key))


def _evidence(properties: Mapping[str, object]) -> tuple[EvidenceEntry, ...]:
    lists = [properties.get(name) for name in EVIDENCE_LISTS]
    if not all(isinstance(values, list) for values in lists):
        raise ValueError(f"an edge needs the lists {', '.join(EVIDENCE_LISTS)}")
    lengths = {len(values) for values in lists}
    if len(lengths) != 1 or lengths == {0}:
        raise ValueError("the evidence lists must be non-empty and the same length")
    return tuple(EvidenceEntry(str(c), str(s), str(f), str(p)) for c, s, f, p in zip(
        properties[CHUNK_IDS], properties[EVIDENCE_SPANS], properties[CONFIDENCES],
        properties[EXTRACT_PROMPTS], strict=True))


def _edge(line: Mapping[str, object]) -> ReviewEdge:
    properties = line["properties"]
    if not isinstance(properties, Mapping):
        raise ValueError("an edge needs a properties object")
    plain = {k: v for k, v in properties.items() if k not in EVIDENCE_LISTS}
    item = str(line["item"])
    role = plain.get("role")
    return ReviewEdge(
        edge_id=item if role is None else f"{item} role={role!r}", item=item,
        type=str(line["type"]), start=MappingProxyType(dict(line["start"])),
        end=MappingProxyType(dict(line["end"])), properties=MappingProxyType(plain),
        evidence=_evidence(properties), start_item=_item(line["start"]),
        end_item=_item(line["end"]),
    )


def _node_properties(line: Mapping[str, object]) -> NodeProperties:
    properties = line["properties"]
    if not isinstance(properties, Mapping):
        raise ValueError("a node needs a properties object")
    return MappingProxyType(dict(properties))


def load_candidates(path: Path) -> Candidates:
    """The edges, node properties and flags in *path*; ValueError names a malformed line."""
    raw = path.read_bytes()
    edges: list[ReviewEdge] = []
    nodes: dict[str, NodeProperties] = {}
    flags: list[tuple[str, str, str]] = []
    for number, text in enumerate(raw.decode("utf-8").splitlines(), start=1):
        try:
            line = json.loads(text)
            if line["kind"] == "edge":
                edges.append(_edge(line))
            elif line["kind"] == "node":
                nodes[str(line["item"])] = _node_properties(line)
            elif line["kind"] == "flag":
                flags.append((str(line["item"]), str(line["chunk_id"]), str(line["reason"])))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path.name} line {number} is not a candidate line: {exc}") from exc
    ids = Counter(e.edge_id for e in edges)
    if dupes := [edge_id for edge_id, n in ids.items() if n > 1]:
        raise ValueError(f"{path.name} lists an edge twice: {dupes[0]}")
    return Candidates(
        accession_no=path.name.split("-run")[0], file_name=path.name,
        sha256=hashlib.sha256(raw).hexdigest(), edges=tuple(edges),
        nodes=MappingProxyType(nodes), flags=tuple(flags),
    )


def quotas(counts: Mapping[str, int], size: int) -> dict[str, int]:
    """Seats per type: one each, the rest by highest averages, never above a type's count."""
    present = sorted((t for t, n in counts.items() if n > 0), key=lambda t: (-counts[t], t))
    seats = {t: 1 for t in present[:size]}
    for _ in range(min(size, sum(counts[t] for t in seats)) - len(seats)):
        open_types = [t for t in seats if seats[t] < counts[t]]
        best = max(open_types, key=lambda t: (counts[t] / (seats[t] + 1), -present.index(t)))
        seats[best] += 1
    return seats


@dataclass(frozen=True)
class Sampler:
    """The seeded draw of one round over the edges not judged in earlier rounds."""

    slots: tuple[str, ...]  # an edge type per slot, in the order shown
    by_type: Mapping[str, tuple[ReviewEdge, ...]]
    any_type: tuple[ReviewEdge, ...]
    rng_tag: str

    @classmethod
    def build(cls, edges: Sequence[ReviewEdge], excluded: Collection[str], size: int,
              seed: int, round_number: int) -> Sampler:
        tag = f"{seed}:round{round_number}"
        rng = random.Random(tag)
        eligible = sorted((e for e in edges if e.edge_id not in excluded), key=lambda e: e.edge_id)
        seats = quotas(Counter(e.type for e in eligible), size)
        slots = [t for t in sorted(seats) for _ in range(seats[t])]
        rng.shuffle(slots)
        by_type = {t: [e for e in eligible if e.type == t] for t in sorted(seats)}
        for group in by_type.values():
            rng.shuffle(group)
        any_type = list(eligible)
        rng.shuffle(any_type)
        return cls(tuple(slots), MappingProxyType({t: tuple(g) for t, g in by_type.items()}),
                   tuple(any_type), tag)

    def draw(self, slot: int, used: Collection[str]) -> ReviewEdge | None:
        """The next unused edge for *slot*: its own type first, then any; None when none."""
        for edge in (*self.by_type[self.slots[slot]], *self.any_type):
            if edge.edge_id not in used:
                return edge
        return None

    def evidence_for(self, edge: ReviewEdge) -> EvidenceEntry:
        """The piece of evidence *edge* is judged against, fixed by the seed."""
        return edge.evidence[random.Random(f"{self.rng_tag}:{edge.edge_id}")
                             .randrange(len(edge.evidence))]
