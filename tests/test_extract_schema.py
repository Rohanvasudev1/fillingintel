"""The structured-output schema derived from the ontology (Step 8).

Limits from docs/research/claude-extraction-structured-output.md, section 1.2:
no numeric or length bounds, minItems only 0 or 1, additionalProperties false
on every object, at most 24 optional and 16 union-typed parameters.
"""
import json

from extract.output_schema import CROSS_FILING_EDGE_TYPES, output_schema
from graph.ontology import CONFIDENCE_LEVELS, EDGE_TYPES, LABELS, Kind

_BOUNDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
           "minLength", "maxLength", "maxItems", "uniqueItems"}
MAX_OPTIONAL = 24
MAX_UNION = 16


def _objects(schema):
    """Every object schema inside *schema*, depth first."""
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


def _subschemas(schema):
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _subschemas(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _subschemas(value)


def _item(name):
    return output_schema()["properties"][name]["items"]["properties"]


def test_labels_are_exactly_the_extracted_labels():
    expected = {d.name for d in LABELS if d.kind is Kind.EXTRACTED}
    enum = _item("nodes")["label"]["enum"]
    assert set(enum) == expected and len(enum) == len(expected)


def test_edge_types_are_the_extracted_edge_types_a_chunk_can_evidence():
    expected = {d.name for d in EDGE_TYPES if d.kind is Kind.EXTRACTED} - CROSS_FILING_EDGE_TYPES
    enum = _item("triples")["type"]["enum"]
    assert set(enum) == expected and len(enum) == len(expected)
    assert CROSS_FILING_EDGE_TYPES == {"PERSISTS_AS"}


def test_confidence_is_the_ontology_enum_on_nodes_and_triples():
    for name in ("nodes", "triples"):
        assert _item(name)["confidence"]["enum"] == list(CONFIDENCE_LEVELS)


def test_schema_has_no_bounds_the_api_rejects():
    for sub in _subschemas(output_schema()):
        assert not _BOUNDS & set(sub), sub
        assert sub.get("minItems", 0) in (0, 1)


def test_every_object_is_closed():
    for obj in _objects(output_schema()):
        assert obj.get("additionalProperties") is False


def test_optional_and_union_parameters_stay_under_the_limits():
    optional = sum(len(set(o["properties"]) - set(o.get("required", ())))
                   for o in _objects(output_schema()))
    unions = sum(1 for s in _subschemas(output_schema())
                 if isinstance(s.get("type"), list) or "anyOf" in s)
    assert optional <= MAX_OPTIONAL
    assert unions <= MAX_UNION


def test_schema_is_plain_json_and_stable():
    assert json.loads(json.dumps(output_schema())) == output_schema()
