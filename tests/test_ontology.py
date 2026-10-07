"""Tests for the ontology module and its schema text (Step 7)."""
import dataclasses
import hashlib
import re
from pathlib import Path

import pytest

from graph.ontology import (
    EDGE_TYPES,
    EDGE_TYPES_BY_NAME,
    LABELS,
    LABELS_BY_NAME,
    ONTOLOGY_VERSION,
    Kind,
    PropType,
)
from graph.schema_text import SCHEMA_TEXT_SHA256, schema_text

CLAUDE_MD = Path(__file__).resolve().parent.parent / "CLAUDE.md"

# One entry per ontology version. Editing a label, edge type, property or
# description changes the schema text: bump ONTOLOGY_VERSION and add its pin here.
PINNED_SCHEMA_SHA256 = {
    1: "9f2687c2aa33872bdf77b64c3b0d80b773cdeac7f7b67dc981892f9bcc57fb37",
}

STRUCTURAL_LABELS = ["Company", "Filing", "Period", "Chunk"]
EXTRACTED_LABELS = [
    "Organization", "Segment", "Product", "RiskFactor", "Regulation",
    "LegalProceeding", "Agreement", "Facility", "Person", "MetricValue",
]
STRUCTURAL_EDGES = {"FILED", "COVERS_PERIOD", "PART_OF", "EVIDENCED_BY"}

REQUIRED_NODE_PROPERTIES = {
    "Company": {"cik", "name", "ticker"},
    "Filing": {"accession_no", "form_type", "filing_date", "fiscal_period"},
    "Period": {"cik", "fiscal_period"},
    "Chunk": {"chunk_id", "accession_no", "section"},
    "RiskFactor": {"key", "title"},
    "MetricValue": {"key", "concept", "value", "unit", "period"},
    **{name: {"key", "name"} for name in [
        "Organization", "Segment", "Product", "Regulation", "LegalProceeding",
        "Agreement", "Facility", "Person",
    ]},
}

KEYS = {
    "Company": ("cik",),
    "Filing": ("accession_no",),
    "Period": ("cik", "fiscal_period"),
    "Chunk": ("chunk_id",),
    **{name: ("key",) for name in EXTRACTED_LABELS},
}


def _props_by_name(definition, *, required=True):
    return {p.name: p for p in definition.properties if p.required is required}


def _claude_md_endpoints():
    """Parse the edge-type table from CLAUDE.md Decisions (Step 7)."""
    line = next(
        (ln for ln in CLAUDE_MD.read_text().splitlines()
         if ln.strip().startswith("- Edge types (start → end):")),
        None,
    )
    assert line, "CLAUDE.md has no '- Edge types (start → end):' line under Decisions"
    table = line.split("):", 1)[1].split(". Any other endpoint pair", 1)[0]
    endpoints = {}
    for entry in table.split(";"):
        entry = re.sub(r"\([^)]*\)", "", entry).strip()
        match = re.fullmatch(r"([A-Z_]+(?:, [A-Z_]+)*) (.+→.+)", entry)
        assert match, f"can't parse CLAUDE.md edge entry {entry!r}"
        names, pair = match.groups()
        start, end = (side.strip() for side in pair.split("→"))
        starts = EXTRACTED_LABELS if start == "extracted node" else start.split("|")
        for name in names.split(","):
            endpoints[name.strip()] = (set(starts), set(end.split("|")))
    return endpoints


# --- labels ---------------------------------------------------------------

def test_labels_are_the_structural_then_extracted_lists():
    assert [d.name for d in LABELS] == STRUCTURAL_LABELS + EXTRACTED_LABELS
    for d in LABELS:
        expected = Kind.STRUCTURAL if d.name in STRUCTURAL_LABELS else Kind.EXTRACTED
        assert d.kind is expected, d.name


def test_community_waits_for_step_12():
    assert "Community" not in LABELS_BY_NAME


@pytest.mark.parametrize("name", STRUCTURAL_LABELS + EXTRACTED_LABELS)
def test_label_required_properties(name):
    required = set(_props_by_name(LABELS_BY_NAME[name]))
    assert required == REQUIRED_NODE_PROPERTIES[name] | {"ontology_version"}
    assert not _props_by_name(LABELS_BY_NAME[name], required=False)


@pytest.mark.parametrize("name", STRUCTURAL_LABELS + EXTRACTED_LABELS)
def test_label_keys(name):
    definition = LABELS_BY_NAME[name]
    assert definition.keys == KEYS[name]
    assert set(definition.keys) <= set(_props_by_name(definition))


def test_chunk_has_no_text_property():
    names = {p.name for p in LABELS_BY_NAME["Chunk"].properties}
    assert "text" not in names


def test_property_types():
    types = {
        (label, p.name): p.type for label in LABELS_BY_NAME
        for p in LABELS_BY_NAME[label].properties
    }
    assert types[("Filing", "filing_date")] is PropType.DATE
    assert types[("Company", "cik")] is PropType.STRING
    assert types[("MetricValue", "value")] is PropType.FLOAT
    for label in EXTRACTED_LABELS:
        assert types[(label, "key")] is PropType.STRING


# --- edge types -----------------------------------------------------------

def test_twenty_edge_types_with_their_kinds():
    assert len(EDGE_TYPES) == 20
    assert len(EDGE_TYPES_BY_NAME) == 20
    for d in EDGE_TYPES:
        expected = Kind.STRUCTURAL if d.name in STRUCTURAL_EDGES else Kind.EXTRACTED
        assert d.kind is expected, d.name


def test_edge_endpoints_match_claude_md():
    documented = _claude_md_endpoints()
    assert set(documented) == set(EDGE_TYPES_BY_NAME)
    for name, (starts, ends) in documented.items():
        d = EDGE_TYPES_BY_NAME[name]
        assert set(d.starts) == starts, name
        assert set(d.ends) == ends, name


def test_endpoints_are_known_labels():
    for d in EDGE_TYPES:
        assert set(d.starts) | set(d.ends) <= set(LABELS_BY_NAME), d.name


def test_extracted_edges_require_evidence_lists():
    for d in EDGE_TYPES:
        required = _props_by_name(d)
        if d.kind is Kind.EXTRACTED:
            assert required["chunk_ids"].type is PropType.LIST_STRING, d.name
            assert required["evidence_spans"].type is PropType.LIST_STRING, d.name
        else:
            assert "chunk_ids" not in required and "evidence_spans" not in required, d.name


def test_evidenced_by_requires_one_span():
    required = _props_by_name(EDGE_TYPES_BY_NAME["EVIDENCED_BY"])
    assert required["evidence_span"].type is PropType.STRING


def test_role_and_stake():
    shared = {"ontology_version", "chunk_ids", "evidence_spans", "evidence_span"}
    edge_specific = {d.name: {p.name for p in d.properties} - shared for d in EDGE_TYPES}
    assert {n: props for n, props in edge_specific.items() if props} == {
        "INVOLVED_IN": {"role"}, "HOLDS_ROLE_AT": {"role"}, "OWNS": {"stake"},
    }
    assert _props_by_name(EDGE_TYPES_BY_NAME["INVOLVED_IN"])["role"].type is PropType.STRING
    assert _props_by_name(EDGE_TYPES_BY_NAME["HOLDS_ROLE_AT"])["role"].type is PropType.STRING
    assert "stake" in _props_by_name(EDGE_TYPES_BY_NAME["OWNS"], required=False)


def test_only_competes_with_is_symmetric():
    assert [d.name for d in EDGE_TYPES if d.symmetric] == ["COMPETES_WITH"]


# --- shared rules ---------------------------------------------------------

def test_every_node_and_edge_requires_ontology_version():
    for d in (*LABELS, *EDGE_TYPES):
        assert _props_by_name(d)["ontology_version"].type is PropType.INTEGER, d.name


def test_names_are_plain_identifiers():
    # Later tickets interpolate labels and edge types into Cypher in backticks.
    for d in LABELS:
        assert re.fullmatch(r"[A-Z][A-Za-z]*", d.name), d.name
    for d in EDGE_TYPES:
        assert re.fullmatch(r"[A-Z]+(_[A-Z]+)*", d.name), d.name
    for d in (*LABELS, *EDGE_TYPES):
        for p in d.properties:
            assert re.fullmatch(r"[a-z]+(_[a-z]+)*", p.name), (d.name, p.name)


def test_every_definition_has_a_one_line_description():
    for d in (*LABELS, *EDGE_TYPES):
        assert d.description.strip() and "\n" not in d.description, d.name


def test_property_names_are_unique_per_definition():
    for d in (*LABELS, *EDGE_TYPES):
        names = [p.name for p in d.properties]
        assert len(names) == len(set(names)), d.name


def test_definitions_cannot_be_changed_at_runtime():
    with pytest.raises(dataclasses.FrozenInstanceError):
        LABELS_BY_NAME["Company"].kind = Kind.EXTRACTED
    with pytest.raises(dataclasses.FrozenInstanceError):
        EDGE_TYPES_BY_NAME["FILED"].ends = ("Chunk",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        LABELS_BY_NAME["Chunk"].properties[0].name = "text"
    with pytest.raises(TypeError):
        LABELS_BY_NAME["Community"] = LABELS[0]
    with pytest.raises(TypeError):
        EDGE_TYPES_BY_NAME["MENTIONS"] = EDGE_TYPES[0]
    with pytest.raises(AttributeError):
        LABELS.append(LABELS[0])
    assert isinstance(LABELS_BY_NAME["Company"].keys, tuple)
    assert isinstance(EDGE_TYPES_BY_NAME["FILED"].starts, tuple)


# --- schema text ----------------------------------------------------------

def test_schema_text_lists_labels_then_edge_types_in_order():
    text = schema_text()
    positions = [text.index(f"\n{d.name} [") for d in LABELS]
    positions += [text.index(f"\n{d.name} [") for d in EDGE_TYPES]
    assert positions == sorted(positions)


def test_schema_text_carries_properties_endpoints_and_descriptions():
    text = schema_text()
    assert f"version {ONTOLOGY_VERSION}" in text
    for d in (*LABELS, *EDGE_TYPES):
        assert d.description in text, d.name
    assert "(Company|Organization)-[:SUPPLIES]->(Company|Organization)" in text
    assert "metric_value" not in text  # labels keep their own spelling
    assert "value: FLOAT" in text
    assert "stake: FLOAT (optional)" in text


def test_schema_text_is_the_same_on_every_call():
    assert schema_text() == schema_text()
    assert SCHEMA_TEXT_SHA256 == hashlib.sha256(schema_text().encode("utf-8")).hexdigest()


def test_schema_hash_is_pinned_for_this_version():
    assert ONTOLOGY_VERSION in PINNED_SCHEMA_SHA256, "add a pin for the new ontology version"
    assert SCHEMA_TEXT_SHA256 == PINNED_SCHEMA_SHA256[ONTOLOGY_VERSION], (
        "the schema text changed: bump ONTOLOGY_VERSION and pin the new hash"
    )
    assert len(set(PINNED_SCHEMA_SHA256.values())) == len(PINNED_SCHEMA_SHA256)
