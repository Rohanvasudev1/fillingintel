"""The graph ontology as frozen data (Step 7).

Every node label and edge type the graph may hold is defined here, with its
kind, keys, properties, allowed endpoints and a one-line description written
by hand. The schema text for prompts, the Neo4j constraints and the write and
validation checks are all derived from this module, so no other file can
define a type. Any edit here changes the schema text and needs a new
ONTOLOGY_VERSION (tests/test_ontology.py pins the hash).

Structural labels and edges come from EDGAR metadata; their evidence is the
filing record. Extracted ones are read from filing text and carry chunk
evidence (ADR-0004): extracted nodes through EVIDENCED_BY edges, extracted
edges through their own chunk_ids and evidence_spans lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

ONTOLOGY_VERSION = 1


class Kind(StrEnum):
    """Structural types come from EDGAR metadata; extracted ones from filing text."""

    STRUCTURAL = "structural"
    EXTRACTED = "extracted"


class PropType(StrEnum):
    """Property types, named as Neo4j's type predicates name them."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    DATE = "DATE"
    LIST_STRING = "LIST<STRING>"


@dataclass(frozen=True, slots=True)
class Prop:
    """One property of a label or edge type."""

    name: str
    type: PropType
    required: bool = True


@dataclass(frozen=True, slots=True)
class LabelDef:
    """One node label; `keys` are the properties its uniqueness constraint covers."""

    name: str
    kind: Kind
    keys: tuple[str, ...]
    properties: tuple[Prop, ...]
    description: str


@dataclass(frozen=True, slots=True)
class EdgeDef:
    """One edge type and the labels allowed at its start and end."""

    name: str
    kind: Kind
    starts: tuple[str, ...]
    ends: tuple[str, ...]
    properties: tuple[Prop, ...]
    description: str
    symmetric: bool = False  # stored one way, traversed in both directions


_VERSION = Prop("ontology_version", PropType.INTEGER)
_KEY = Prop("key", PropType.STRING)
_NAME = Prop("name", PropType.STRING)
_EDGE_EVIDENCE = (
    Prop("chunk_ids", PropType.LIST_STRING),
    Prop("evidence_spans", PropType.LIST_STRING),
)


def _structural(
    name: str, keys: tuple[str, ...], props: tuple[Prop, ...], description: str,
) -> LabelDef:
    return LabelDef(name, Kind.STRUCTURAL, keys, (*props, _VERSION), description)


def _extracted(name: str, description: str, props: tuple[Prop, ...] = (_NAME,)) -> LabelDef:
    return LabelDef(name, Kind.EXTRACTED, ("key",), (_KEY, *props, _VERSION), description)


LABELS: tuple[LabelDef, ...] = (
    _structural(
        "Company", ("cik",),
        (Prop("cik", PropType.STRING), Prop("name", PropType.STRING),
         Prop("ticker", PropType.STRING)),
        "One of the three filers (NVIDIA, AMD, Intel), identified by its SEC CIK.",
    ),
    _structural(
        "Filing", ("accession_no",),
        (Prop("accession_no", PropType.STRING), Prop("form_type", PropType.STRING),
         Prop("filed_date", PropType.DATE), Prop("fiscal_period", PropType.STRING)),
        "A 10-K or 10-Q in the corpus, identified by its EDGAR accession number.",
    ),
    _structural(
        "Period", ("cik", "fiscal_period"),
        (Prop("cik", PropType.STRING), Prop("fiscal_period", PropType.STRING)),
        "A fiscal year or quarter of one filer; the same label at two filers covers "
        "different dates.",
    ),
    _structural(
        "Chunk", ("chunk_id",),
        (Prop("chunk_id", PropType.STRING), Prop("accession_no", PropType.STRING),
         Prop("section", PropType.STRING)),
        "A passage of a filing that evidence points at; its text stays in Postgres.",
    ),
    _extracted(
        "Organization",
        "Any company or body a filing names other than the three filers, such as a "
        "supplier, customer, competitor, partner, subsidiary or acquired business.",
    ),
    _extracted("Segment", "A reportable business segment of a filer."),
    _extracted(
        "Product", "A named product, product family or chip architecture, such as Blackwell.",
    ),
    _extracted(
        "RiskFactor",
        "One risk factor as disclosed in one filing; the same risk a year later is a "
        "separate node.",
        (Prop("title", PropType.STRING),),
    ),
    _extracted(
        "Regulation",
        "A law, rule or government measure, such as an export control or a tax regime.",
    ),
    _extracted("LegalProceeding", "A lawsuit, investigation or other legal or regulatory case."),
    _extracted(
        "Agreement", "A contract or deal, such as an acquisition, investment or supply agreement.",
    ),
    _extracted("Facility", "A physical site such as a fab, plant or data center."),
    _extracted("Person", "A named individual, such as an executive or a director."),
    _extracted(
        "MetricValue",
        "One reported figure for one subject and period, as stated in one filing; a "
        "restated figure is a separate node.",
        (Prop("concept", PropType.STRING), Prop("value", PropType.FLOAT),
         Prop("unit", PropType.STRING), Prop("period", PropType.STRING)),
    ),
)

_EXTRACTED_LABEL_NAMES = tuple(d.name for d in LABELS if d.kind is Kind.EXTRACTED)
_FIRMS = ("Company", "Organization")


def _structural_edge(
    name: str, starts: tuple[str, ...], ends: tuple[str, ...], description: str,
    props: tuple[Prop, ...] = (),
) -> EdgeDef:
    return EdgeDef(name, Kind.STRUCTURAL, starts, ends, (*props, _VERSION), description)


def _extracted_edge(
    name: str, starts: tuple[str, ...], ends: tuple[str, ...], description: str,
    props: tuple[Prop, ...] = (), symmetric: bool = False,
) -> EdgeDef:
    return EdgeDef(
        name, Kind.EXTRACTED, starts, ends, (*props, *_EDGE_EVIDENCE, _VERSION),
        description, symmetric,
    )


_ROLE = Prop("role", PropType.STRING)

EDGE_TYPES: tuple[EdgeDef, ...] = (
    _structural_edge("FILED", ("Company",), ("Filing",), "The filer submitted this filing."),
    _structural_edge(
        "COVERS_PERIOD", ("Filing",), ("Period",), "The fiscal period the filing reports on.",
    ),
    _structural_edge("PART_OF", ("Chunk",), ("Filing",), "The filing the chunk was cut from."),
    _structural_edge(
        "EVIDENCED_BY", _EXTRACTED_LABEL_NAMES, ("Chunk",),
        "A chunk the node was read from, with the quote that names it.",
        (Prop("evidence_span", PropType.STRING),),
    ),
    _extracted_edge(
        "HAS_SEGMENT", ("Company",), ("Segment",), "The filer reports this business segment.",
    ),
    _extracted_edge(
        "OFFERS", ("Company", "Segment"), ("Product",), "The filer or segment sells this product.",
    ),
    _extracted_edge(
        "DISCLOSES", ("Filing",), ("RiskFactor",), "The filing lists this risk factor.",
    ),
    _extracted_edge(
        "AFFECTS", ("RiskFactor",), ("Segment", "Product"),
        "The risk factor names this segment or product as exposed.",
    ),
    _extracted_edge(
        "PERSISTS_AS", ("RiskFactor",), ("RiskFactor",),
        "The same risk factor in a later filing; evidence is the chunks of both.",
    ),
    _extracted_edge(
        "REPORTS", ("Filing",), ("MetricValue",), "The filing states this figure.",
    ),
    _extracted_edge(
        "MEASURES", ("MetricValue",), ("Company", "Segment", "Product"),
        "The subject the figure is about.",
    ),
    _extracted_edge(
        "SUPPLIES", _FIRMS, _FIRMS, "The start firm supplies goods or services to the end firm.",
    ),
    _extracted_edge(
        "CUSTOMER_OF", _FIRMS, _FIRMS, "The start firm buys from the end firm.",
    ),
    _extracted_edge(
        "COMPETES_WITH", _FIRMS, _FIRMS,
        "The two firms compete; stored one way, read in both directions.",
        symmetric=True,
    ),
    _extracted_edge(
        "OWNS", _FIRMS, ("Organization", "Facility"),
        "The start firm owns all or part of the organization or facility; stake is the "
        "owned share from 0 to 1, when stated.",
        (Prop("stake", PropType.FLOAT, required=False),),
    ),
    _extracted_edge(
        "PARTY_TO", _FIRMS, ("Agreement",), "The firm is a party to the agreement.",
    ),
    _extracted_edge(
        "CONCERNS", ("Agreement",), ("Organization", "Product", "Facility"),
        "The organization, product or facility the agreement is about.",
    ),
    _extracted_edge(
        "SUBJECT_TO", ("Company", "Product"), ("Regulation",),
        "The filer or product falls under the regulation.",
    ),
    _extracted_edge(
        "INVOLVED_IN", _FIRMS, ("LegalProceeding",),
        "The firm is part of the case, in the role given (such as plaintiff or defendant).",
        (_ROLE,),
    ),
    _extracted_edge(
        "HOLDS_ROLE_AT", ("Person",), ("Company",),
        "The person holds the role given at the filer (such as CEO or director).",
        (_ROLE,),
    ),
)

LABELS_BY_NAME: MappingProxyType[str, LabelDef] = MappingProxyType({d.name: d for d in LABELS})
EDGE_TYPES_BY_NAME: MappingProxyType[str, EdgeDef] = MappingProxyType(
    {d.name: d for d in EDGE_TYPES}
)
