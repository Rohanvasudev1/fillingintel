"""The graph builder (Step 10): one write-path batch from every input, built in Python.

build_graph() is pure: no database, no network. It does all the combining, so
the write path writes exactly what it is given (ADR-0004, ADR-0006):

- Company, Filing, Period and Chunk nodes, and FILED, COVERS_PERIOD and
  PART_OF edges, come from the structural rows. Chunk nodes hold no text.
- Every extract file must carry one prompt version, of the current extract
  prompt; a mix is refused.
- Every extracted key maps through the alias map; a key absent from it is its
  own canonical key. A key mapped to `Company:{cik}` is that filer's Company
  node, so the extracted node is not emitted and its edges move to the Company.
- A merged node takes its canonical member's own properties, from its latest
  filing; the other members' names go in `aliases`, sorted and without repeats.
- Node and edge evidence hold one entry per chunk, ordered by the chunk's
  filing date, then chunk ID. When a chunk appears twice the more confident
  entry wins, ties to the later filing, then the later line.
- A `stake` that differs across an edge's members takes the most confident
  member's value, ties to the later filing; each disagreement is a conflict.
- A MetricValue with no MEASURES edge in its own file is left out with every
  edge touching it, and listed. An edge a merge turns into a self-loop, or
  whose new endpoint its type does not allow, is dropped and listed.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from extract.filers import TICKER_BY_CIK
from extract.outcomes import Conflict
from graph.batch import (
    CHUNK_IDS,
    CONFIDENCES,
    EVIDENCE_SPANS,
    EXTRACT_PROMPTS,
    Batch,
    EdgeRecord,
    Evidence,
    NodeRecord,
    NodeRef,
)
from graph.build_input import (
    EXTRACT_PROMPT_PREFIX,
    ExtractEdge,
    ExtractFile,
    ExtractNode,
    StructuralRows,
)
from graph.ontology import CONFIDENCE_RANK, EDGE_TYPES_BY_NAME, LABELS_BY_NAME, Kind

# Looked up in the ontology, so renaming one there fails at import, not silently.
COMPANY, FILING, PERIOD, CHUNK, METRIC = (
    LABELS_BY_NAME[name].name for name in ("Company", "Filing", "Period", "Chunk", "MetricValue"))
FILED, COVERS_PERIOD, PART_OF, MEASURES = (
    EDGE_TYPES_BY_NAME[name].name for name in ("FILED", "COVERS_PERIOD", "PART_OF", "MEASURES"))
ALIASES, STAKE = "aliases", "stake"
COMPANY_KEY_PREFIX = f"{COMPANY}:"  # how the alias map names a filer (ADR-0006)
_NAME_PROPERTY = {"RiskFactor": "title", METRIC: "concept"}


class BuildError(ValueError):
    """The inputs disagree with each other; the message names the item."""


class PromptMix(BuildError):
    """The extract files were not all made under the current extract prompt version."""


class DropReason(StrEnum):
    SKIPPED_METRIC_VALUE = "skipped_metric_value"
    SELF_LOOP = "self_loop"
    DISALLOWED_ENDPOINT = "disallowed_endpoint"


@dataclass(frozen=True, slots=True)
class DroppedEdge:
    reason: DropReason
    edge: str  # start-[TYPE]->end, after alias mapping
    file: str


@dataclass(frozen=True, slots=True)
class BuildReport:
    conflicts: tuple[Conflict, ...]
    skipped_metric_values: tuple[str, ...]
    dropped_edges: tuple[DroppedEdge, ...]
    merged_into_company: tuple[str, ...]  # extracted keys the alias map sends to a filer


@dataclass(frozen=True, slots=True)
class Built:
    batch: Batch
    report: BuildReport


@dataclass(frozen=True, slots=True)
class _Entry:
    """One piece of evidence with where it came from, for ordering and ties."""

    evidence: Evidence
    rank: tuple[int, date, int, int]  # confidence, file's filing date, file index, line

    @property
    def chunk_id(self) -> str:
        return self.evidence.chunk_id


@dataclass(frozen=True, slots=True)
class _Context:
    """What every step reads: filing dates, chunk dates, the alias map, skipped metrics."""

    filing_dates: Mapping[str, date]
    chunk_dates: Mapping[str, date]
    alias_map: Mapping[str, str]
    skipped: frozenset[str]

    def canonical(self, ref: NodeRef) -> NodeRef:
        """*ref* with an extracted key mapped; a key mapped to a filer names its Company."""
        if LABELS_BY_NAME[ref.label].kind is not Kind.EXTRACTED:
            return ref
        key = str(ref.key_dict()["key"])
        mapped = self.alias_map.get(key, key)
        if mapped.startswith(COMPANY_KEY_PREFIX):
            return NodeRef.of(COMPANY, cik=mapped.removeprefix(COMPANY_KEY_PREFIX))
        return NodeRef.of(ref.label, key=mapped)

    def entries(self, evidence: Iterable[Evidence], file_index: int, line: int,
                file_date: date) -> list[_Entry]:
        found = []
        for e in evidence:
            if e.chunk_id not in self.chunk_dates:
                raise BuildError(f"evidence cites chunk {e.chunk_id!r}, which Postgres lacks")
            found.append(_Entry(e, (CONFIDENCE_RANK[e.confidence], file_date, file_index, line)))
        return found

    def ordered(self, entries: Iterable[_Entry]) -> tuple[Evidence, ...]:
        """One entry per chunk, the highest-ranked; ordered by the chunk's filing, then ID."""
        best: dict[str, _Entry] = {}
        for entry in entries:
            if entry.chunk_id not in best or entry.rank > best[entry.chunk_id].rank:
                best[entry.chunk_id] = entry
        return tuple(e.evidence for e in sorted(
            best.values(), key=lambda e: (self.chunk_dates[e.chunk_id], e.chunk_id)))


def build_graph(
    rows: StructuralRows, files: Sequence[ExtractFile], alias_map: Mapping[str, str],
) -> Built:
    """The batch that is the whole graph, and what the build left out or found."""
    _check_prompts(files)
    filing_dates = {f.accession_no: f.filing_date for f in rows.filings}
    context = _Context(
        filing_dates=filing_dates,
        chunk_dates={c.chunk_id: _filing_date(filing_dates, c.accession_no) for c in rows.chunks},
        alias_map=alias_map,
        skipped=_metrics_without_subject(files),
    )
    ordered = sorted(files, key=lambda f: (_filing_date(filing_dates, f.accession_no),
                                           f.accession_no))
    nodes, into_company = _nodes(ordered, context)
    edges, conflicts, dropped = _edges(ordered, context, frozenset(nodes))
    return Built(
        Batch(nodes=(*structural_nodes(rows), *(nodes[r] for r in sorted(nodes, key=str))),
              edges=(*structural_edges(rows), *edges)),
        BuildReport(tuple(conflicts), tuple(sorted(context.skipped)), tuple(dropped),
                    tuple(sorted(into_company))),
    )


def _filing_date(filing_dates: Mapping[str, date], accession_no: str) -> date:
    if accession_no not in filing_dates:
        raise BuildError(f"filing {accession_no} is not in Postgres")
    return filing_dates[accession_no]


def _check_prompts(files: Sequence[ExtractFile]) -> None:
    prompts = {p for f in files for p in f.prompts}
    if len(prompts) > 1 or any(not p.startswith(EXTRACT_PROMPT_PREFIX) for p in prompts):
        raise PromptMix(f"expected one {EXTRACT_PROMPT_PREFIX}… prompt version across the "
                        f"extract files, found {', '.join(sorted(prompts))}")


def _metrics_without_subject(files: Sequence[ExtractFile]) -> frozenset[str]:
    """Keys of MetricValues whose own file has no MEASURES edge from them."""
    skipped: set[str] = set()
    for f in files:
        measured = {e.start.key_dict().get("key") for e in f.edges if e.type == MEASURES}
        skipped |= {n.key for n in f.nodes if n.label == METRIC and n.key not in measured}
    return frozenset(skipped)


# ── extracted nodes ───────────────────────────────────────────────────────────

def _nodes(
    files: Sequence[ExtractFile], context: _Context,
) -> tuple[dict[NodeRef, NodeRecord], set[str]]:
    """One record per canonical node, and the keys merged into a filer's Company."""
    members: dict[NodeRef, list[tuple[ExtractNode, list[_Entry]]]] = {}
    into_company: set[str] = set()
    for index, f in enumerate(files):
        file_date = context.filing_dates[f.accession_no]
        for line, node in enumerate(f.nodes):
            if node.key in context.skipped:
                continue
            ref = context.canonical(NodeRef.of(node.label, key=node.key))
            entries = context.entries(node.evidence, index, line, file_date)
            if ref.label == COMPANY:
                into_company.add(node.key)
            else:
                members.setdefault(ref, []).append((node, entries))
    return {ref: _node_record(ref, group, context) for ref, group in members.items()}, into_company


def _node_record(
    ref: NodeRef, group: Sequence[tuple[ExtractNode, list[_Entry]]], context: _Context,
) -> NodeRecord:
    key = str(ref.key_dict()["key"])
    canonical = [n for n, _ in group if n.key == key]
    if not canonical:
        raise BuildError(f"the alias map sends keys to {key!r}, which no extract file holds")
    properties = dict(canonical[-1].properties)  # files run oldest first: the latest wins
    if any(p.name == ALIASES for p in LABELS_BY_NAME[ref.label].properties):
        name_property = _NAME_PROPERTY.get(ref.label, "name")
        names = {str(n.properties[name_property]) for n, _ in group}
        names |= {str(a) for n, _ in group for a in n.properties.get(ALIASES) or ()}
        names.discard(str(properties[name_property]))
        properties.pop(ALIASES, None)
        if names:
            properties[ALIASES] = sorted(names)
    return NodeRecord(ref.label, properties, context.ordered(e for _, es in group for e in es))


# ── extracted edges ───────────────────────────────────────────────────────────

_EdgeId = tuple[str, NodeRef, NodeRef, str | None]


def _edges(
    files: Sequence[ExtractFile], context: _Context, nodes: frozenset[NodeRef],
) -> tuple[list[EdgeRecord], list[Conflict], list[DroppedEdge]]:
    members: dict[_EdgeId, list[tuple[ExtractEdge, list[_Entry]]]] = {}
    dropped: list[DroppedEdge] = []
    for index, f in enumerate(files):
        file_date = context.filing_dates[f.accession_no]
        for line, edge in enumerate(f.edges):
            start, end = context.canonical(edge.start), context.canonical(edge.end)
            reason = _drop_reason(edge, start, end, context.skipped)
            if reason is not None:
                dropped.append(DroppedEdge(reason, _name(edge.type, start, end), f.name))
                continue
            _require_nodes(edge.type, (start, end), nodes, f.name)
            if EDGE_TYPES_BY_NAME[edge.type].symmetric and str(end) < str(start):
                start, end = end, start
            entries = context.entries(edge.evidence, index, line, file_date)
            members.setdefault((edge.type, start, end, edge.role), []).append((edge, entries))
    records, conflicts = [], []
    for identity in sorted(members, key=str):
        record, found = _edge_record(identity, members[identity], context)
        records.append(record)
        conflicts.extend(found)
    return records, conflicts, dropped


def _name(edge_type: str, start: NodeRef, end: NodeRef) -> str:
    return f"{start}-[{edge_type}]->{end}"


def _drop_reason(
    edge: ExtractEdge, start: NodeRef, end: NodeRef, skipped: frozenset[str],
) -> DropReason | None:
    if any(r.label == METRIC and r.key_dict()["key"] in skipped for r in (edge.start, edge.end)):
        return DropReason.SKIPPED_METRIC_VALUE
    if start == end:
        return DropReason.SELF_LOOP
    definition = EDGE_TYPES_BY_NAME[edge.type]
    if start.label not in definition.starts or end.label not in definition.ends:
        return DropReason.DISALLOWED_ENDPOINT
    return None


def _require_nodes(
    edge_type: str, ends: Iterable[NodeRef], nodes: frozenset[NodeRef], file: str,
) -> None:
    for ref in ends:
        if LABELS_BY_NAME[ref.label].kind is Kind.EXTRACTED and ref not in nodes:
            raise BuildError(f"{file}: a {edge_type} edge names {ref}, which no node line holds")


def _edge_record(
    identity: _EdgeId, group: Sequence[tuple[ExtractEdge, list[_Entry]]], context: _Context,
) -> tuple[EdgeRecord, list[Conflict]]:
    edge_type, start, end, role = identity
    evidence = context.ordered(e for _, es in group for e in es)
    properties: dict[str, object] = {
        CHUNK_IDS: tuple(e.chunk_id for e in evidence),
        EVIDENCE_SPANS: tuple(e.span for e in evidence),
        CONFIDENCES: tuple(e.confidence for e in evidence),
        EXTRACT_PROMPTS: tuple(e.extract_prompt for e in evidence),
    }
    if role is not None:
        properties["role"] = role
    stake, conflicts = _stake(_name(edge_type, start, end), group)
    if stake is not None:
        properties[STAKE] = stake
    return EdgeRecord(edge_type, start, end, properties), conflicts


def _stake(
    item: str, group: Sequence[tuple[ExtractEdge, list[_Entry]]],
) -> tuple[float | None, list[Conflict]]:
    """The winning stake across members, ranked by each member's best entry, and conflicts."""
    stakes = [(edge.stake, max(entries, key=lambda e: e.rank))
              for edge, entries in group if edge.stake is not None]
    if not stakes:
        return None, []
    value, best = max(stakes, key=lambda s: s[1].rank)
    losers: dict[float, _Entry] = {}  # one conflict per losing value, its best entry
    for other, entry in stakes:
        if other != value and (other not in losers or entry.rank > losers[other].rank):
            losers[other] = entry
    return value, [Conflict(item, STAKE, value, other, best.chunk_id, entry.chunk_id)
                   for other, entry in sorted(losers.items())]


# ── structural nodes and edges ────────────────────────────────────────────────

def structural_nodes(rows: StructuralRows) -> tuple[NodeRecord, ...]:
    """Company (name from its latest filing), Filing, Period and Chunk nodes."""
    filings = sorted(rows.filings, key=lambda f: (f.filing_date, f.accession_no))
    for f in filings:
        if f.cik not in TICKER_BY_CIK:
            raise BuildError(f"filing {f.accession_no} is by CIK {f.cik}, not one of the filers")
    companies = {f.cik: f.company_name for f in filings}  # the latest filing's name wins
    return (
        *(NodeRecord(COMPANY, {"cik": cik, "name": name, "ticker": TICKER_BY_CIK[cik]})
          for cik, name in sorted(companies.items())),
        *(NodeRecord(FILING, {"accession_no": f.accession_no, "form_type": f.form_type,
                              "filing_date": f.filing_date, "fiscal_period": f.fiscal_period})
          for f in sorted(filings, key=lambda f: f.accession_no)),
        *(NodeRecord(PERIOD, {"cik": cik, "fiscal_period": period})
          for cik, period in sorted({(f.cik, f.fiscal_period) for f in filings})),
        *(NodeRecord(CHUNK, {"chunk_id": c.chunk_id, "accession_no": c.accession_no,
                             "section": c.section})
          for c in sorted(rows.chunks, key=lambda c: c.chunk_id)),
    )


def structural_edges(rows: StructuralRows) -> tuple[EdgeRecord, ...]:
    filings = sorted(rows.filings, key=lambda f: f.accession_no)
    return (
        *(EdgeRecord(FILED, NodeRef.of(COMPANY, cik=f.cik),
                     NodeRef.of(FILING, accession_no=f.accession_no)) for f in filings),
        *(EdgeRecord(COVERS_PERIOD, NodeRef.of(FILING, accession_no=f.accession_no),
                     NodeRef.of(PERIOD, cik=f.cik, fiscal_period=f.fiscal_period))
          for f in filings),
        *(EdgeRecord(PART_OF, NodeRef.of(CHUNK, chunk_id=c.chunk_id),
                     NodeRef.of(FILING, accession_no=c.accession_no))
          for c in sorted(rows.chunks, key=lambda c: c.chunk_id)),
    )
