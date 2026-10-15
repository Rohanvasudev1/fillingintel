"""What a community report is written from (Step 12). Pure.

A lower community's report reads:
- every member, with its label and name (a RiskFactor's title);
- the extracted edges with both ends members, most evidence chunks first
  (ties by type and ends), each with at most `spans_per_edge` evidence spans,
  while they fit in `edges` tokens;
- passages: the members' evidence chunks, ranked by how many members and
  member edges cite them (ties to the smaller chunk ID), with the text
  resolve() returns, while the whole input fits in `total` tokens. The first
  passage is always given, so a report always has a chunk it may cite.

Tokens are counted with cl100k_base per rendered line or passage, plus one
for its line break. The members' evidence chunks are the community's
evidence: a lower report may cite only those.

A top community's report reads its lower reports' kept text, and may cite
only the chunks those reports kept. A lower report with no kept text is left
out.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType

from graph.batch import CHUNK_IDS, EVIDENCE_SPANS, Batch
from graph.community import Community
from graph.community_graph import KEY, METRIC
from graph.ontology import EDGE_TYPES_BY_NAME, LABELS_BY_NAME, Kind
from ingest.chunker import count_tokens

NAME_PROPERTIES = ("name", "title")


@dataclass(frozen=True, slots=True)
class InputBudget:
    total: int = 6_000  # tokens for members, edges and passages together
    edges: int = 1_500  # tokens for the edge lines
    spans_per_edge: int = 2


DEFAULT_BUDGET = InputBudget()


@dataclass(frozen=True, slots=True)
class Member:
    key: str
    label: str
    name: str


@dataclass(frozen=True, slots=True)
class MemberEdge:
    type: str
    start: str  # key
    end: str  # key
    chunk_ids: tuple[str, ...]
    spans: tuple[str, ...]  # parallel to chunk_ids until capped


@dataclass(frozen=True, slots=True)
class Passage:
    chunk_id: str
    text: str


@dataclass(frozen=True, slots=True)
class LowerInput:
    community_id: str
    members: tuple[Member, ...]  # by key
    edges: tuple[MemberEdge, ...]
    passages: tuple[Passage, ...]
    evidence: frozenset[str]  # the members' evidence chunks


@dataclass(frozen=True, slots=True)
class ChildReport:
    community_id: str
    title: str
    text: str  # the kept report text
    chunk_ids: tuple[str, ...]  # the chunks its kept sentences cite


@dataclass(frozen=True, slots=True)
class TopInput:
    community_id: str
    children: tuple[ChildReport, ...]  # lower reports with kept text, in the given order
    evidence: frozenset[str]  # the chunks those reports kept


@dataclass(frozen=True, slots=True)
class _Node:
    label: str
    name: str
    chunk_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class CommunityIndex:
    """The community graph's nodes and extracted edges, as reports read them."""

    nodes: Mapping[str, _Node]
    edges: tuple[MemberEdge, ...]

    @classmethod
    def from_batch(cls, batch: Batch) -> CommunityIndex:
        nodes = {str(n.properties[KEY]): _Node(n.label, _name(n.properties),
                                               frozenset(e.chunk_id for e in n.evidence))
                 for n in batch.nodes if _kept(n.label)}
        edges = tuple(
            MemberEdge(e.type, str(e.start.key_dict()[KEY]), str(e.end.key_dict()[KEY]),
                       tuple(e.properties[CHUNK_IDS]),  # type: ignore[arg-type]
                       tuple(e.properties[EVIDENCE_SPANS]))  # type: ignore[arg-type]
            for e in batch.edges
            if EDGE_TYPES_BY_NAME[e.type].kind is Kind.EXTRACTED
            and _kept(e.start.label) and _kept(e.end.label)
        )
        return cls(MappingProxyType(nodes), edges)


def _kept(label: str) -> bool:
    return LABELS_BY_NAME[label].kind is Kind.EXTRACTED and label != METRIC


def _name(properties: Mapping[str, object]) -> str:
    for prop in NAME_PROPERTIES:
        if isinstance(value := properties.get(prop), str):
            return value
    raise ValueError(f"node {properties.get(KEY)!r} has no name or title")


def member_line(member: Member) -> str:
    return f"- {member.label}: {member.name}"


def edge_line(edge: MemberEdge, members: Mapping[str, Member]) -> str:
    start, end = members[edge.start], members[edge.end]
    spans = "; ".join(f'"{s}"' for s in edge.spans)
    return (f'- {start.label} "{start.name}" -[{edge.type}]-> {end.label} "{end.name}": '
            f"{spans}")


def passage_block(passage: Passage) -> str:
    return f'<passage id="{passage.chunk_id}">\n{passage.text}\n</passage>'


def _cost(text: str) -> int:
    return count_tokens(text) + 1


def rendered_tokens(built: LowerInput) -> int:
    """The tokens the input's rendered lines and passages count, as the budget counts them."""
    by_key = {m.key: m for m in built.members}
    pieces = (*(member_line(m) for m in built.members),
              *(edge_line(e, by_key) for e in built.edges),
              *(passage_block(p) for p in built.passages))
    return sum(_cost(p) for p in pieces)


def ranked_chunks(community: Community, index: CommunityIndex) -> tuple[str, ...]:
    """The members' evidence chunks, most cited by members and member edges first."""
    members = set(community.members)
    evidence = {c for k in members for c in index.nodes[k].chunk_ids}
    counts: Counter[str] = Counter()
    for key in members:
        counts.update(index.nodes[key].chunk_ids)
    for edge in _member_edges(members, index):
        counts.update(c for c in set(edge.chunk_ids) if c in evidence)
    return tuple(sorted(evidence, key=lambda c: (-counts[c], c)))


def _member_edges(members: set[str], index: CommunityIndex) -> list[MemberEdge]:
    edges = [e for e in index.edges if e.start in members and e.end in members]
    return sorted(edges, key=lambda e: (-len(e.chunk_ids), e.type, e.start, e.end))


def lower_input(community: Community, index: CommunityIndex, texts: Mapping[str, str],
                budget: InputBudget = DEFAULT_BUDGET) -> LowerInput:
    """What *community*'s report is written from; ValueError when a chunk's text is missing."""
    ranked = ranked_chunks(community, index)
    if missing := [c for c in ranked if c not in texts]:
        raise ValueError(f"no text for chunk {missing[0]} ({len(missing)} missing)")
    members = tuple(Member(k, index.nodes[k].label, index.nodes[k].name)
                    for k in sorted(community.members))
    by_key = {m.key: m for m in members}
    used = sum(_cost(member_line(m)) for m in members)
    edges, edge_used = [], 0
    for edge in _member_edges(set(by_key), index):
        capped = replace(edge, spans=edge.spans[:budget.spans_per_edge])
        cost = _cost(edge_line(capped, by_key))
        if edge_used + cost <= budget.edges and used + cost <= budget.total:
            edges.append(capped)
            edge_used += cost
            used += cost
    passages: list[Passage] = []
    for chunk_id in ranked:
        passage = Passage(chunk_id, texts[chunk_id])
        cost = _cost(passage_block(passage))
        if not passages or used + cost <= budget.total:
            passages.append(passage)
            used += cost
    return LowerInput(community.community_id, members, tuple(edges), tuple(passages),
                      frozenset(ranked))


def top_input(community: Community, children: Sequence[ChildReport]) -> TopInput:
    """What *community*'s report is written from: its lower reports that kept any text."""
    kept = tuple(c for c in children if c.text)
    return TopInput(community.community_id, kept,
                    frozenset(cid for c in kept for cid in c.chunk_ids))
