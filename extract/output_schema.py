"""The structured-output JSON schema, derived from the ontology (Step 8).

The model returns, per chunk, candidate nodes and candidate triples. Labels,
edge types and confidence levels are enums taken from graph.ontology, so the
grammar keeps the model inside the ontology (casing aside, which code compares
case-insensitively). Only extracted types are offered: structural nodes come
from EDGAR metadata, and the three filers and this filing are named by filer
references. PERSISTS_AS links a risk factor across filings, so one chunk cannot
evidence it; Step 10 builds it.

The schema keeps inside the API's limits (research note, section 1.2): every
field is required, with null standing for "not used"; no numeric or length
bounds; five union-typed fields. Every other rule is checked in code.
"""
from __future__ import annotations

import copy

from graph.ontology import CONFIDENCE_LEVELS, EDGE_TYPES, LABELS, Kind

CROSS_FILING_EDGE_TYPES = frozenset({"PERSISTS_AS"})
EXTRACTABLE_LABELS: tuple[str, ...] = tuple(d.name for d in LABELS if d.kind is Kind.EXTRACTED)
EXTRACTABLE_EDGE_TYPES: tuple[str, ...] = tuple(
    d.name for d in EDGE_TYPES
    if d.kind is Kind.EXTRACTED and d.name not in CROSS_FILING_EDGE_TYPES
)


def _string(description: str) -> dict[str, object]:
    return {"type": "string", "description": description}


def _nullable(json_type: str, description: str) -> dict[str, object]:
    return {"type": [json_type, "null"], "description": description}


def _closed(properties: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


_EVIDENCE = {
    "evidence_span": _string("Words copied exactly from the passage that state this."),
    "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
}

_NODE = _closed({
    "id": _string("A local ID for this node in this reply, such as n1."),
    "label": {"type": "string", "enum": list(EXTRACTABLE_LABELS)},
    "name": _string("The name as printed; a RiskFactor's title; a MetricValue's metric."),
    "value": _nullable("number", "MetricValue only: the figure in its unit."),
    "unit": _nullable("string", "MetricValue only: the unit, such as USD millions."),
    "period": _nullable("string", "MetricValue only: the fiscal period, such as FY2026."),
    **_EVIDENCE,
})

_TRIPLE = _closed({
    "start": _string("A local node ID or a filer reference."),
    "type": {"type": "string", "enum": list(EXTRACTABLE_EDGE_TYPES)},
    "end": _string("A local node ID or a filer reference."),
    "role": _nullable("string", "INVOLVED_IN and HOLDS_ROLE_AT only: the role."),
    "stake": _nullable("number", "OWNS only: the owned share from 0 to 1, when stated."),
    **_EVIDENCE,
})


def output_schema() -> dict[str, object]:
    """The JSON schema for one chunk's reply; a new dict on every call."""
    return copy.deepcopy(_closed({
        "nodes": {"type": "array", "items": _NODE},
        "triples": {"type": "array", "items": _TRIPLE},
    }))
