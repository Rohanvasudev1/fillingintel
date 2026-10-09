"""Tests for the pure batch check (Step 7).

Each test starts from the shared valid test graph and breaks one thing, so it
shows one rule on its own. No database.
"""
from dataclasses import replace

from graph.batch import Batch, EdgeRecord, Evidence, NodeRecord, NodeRef, Rule, check_batch
from tests.graph_test_data import (
    COMPUTE,
    CUSTOMERS_CHUNK,
    EXPORT_CHUNK,
    FILING,
    NVDA_10K,
    NVIDIA,
    PERIOD,
    QUOTES,
    SUPPLY_CHUNK,
    TEST_PROMPT,
    TSMC,
    edge_evidence,
)

UNKNOWN_CHUNK_ID = f"{NVDA_10K}:9999"


def _rules(batch, **existing):
    return [v.rule for v in check_batch(batch, **existing)]


def _with_node(batch, node):
    return replace(batch, nodes=(*batch.nodes, node))


def _with_edge(batch, edge):
    return replace(batch, edges=(*batch.edges, edge))


def _replace_node(batch, label, change):
    """*batch* with the first *label* node passed through *change*."""
    index = next(i for i, n in enumerate(batch.nodes) if n.label == label)
    nodes = list(batch.nodes)
    nodes[index] = change(nodes[index])
    return replace(batch, nodes=tuple(nodes))


def _replace_edge(batch, edge_type, change):
    index = next(i for i, e in enumerate(batch.edges) if e.type == edge_type)
    edges = list(batch.edges)
    edges[index] = change(edges[index])
    return replace(batch, edges=tuple(edges))


def _set_props(record, **changes):
    return replace(record, properties={**record.properties, **changes})


def _drop_prop(record, name):
    return replace(record, properties={k: v for k, v in record.properties.items() if k != name})


def _samsung():
    return NodeRecord(
        "Organization", {"key": "org:samsung", "name": "Samsung Electronics Co., Ltd."},
        (Evidence(SUPPLY_CHUNK, "Samsung Electronics Co", "stated", TEST_PROMPT),),
    )


# ── the valid graph ────────────────────────────────────────────────────────────

def test_the_valid_test_graph_has_no_violations(graph_test_batch):
    assert check_batch(graph_test_batch) == ()


def test_an_empty_batch_has_no_violations():
    assert check_batch(Batch()) == ()


# ── labels, edge types and endpoints ───────────────────────────────────────────

def test_an_unknown_label_is_rejected(graph_test_batch):
    batch = _with_node(graph_test_batch, NodeRecord(
        "Supplier", {"key": "org:tsmc", "name": "TSMC"},
        (Evidence(SUPPLY_CHUNK, "TSMC", "stated", TEST_PROMPT),),
    ))
    assert _rules(batch) == [Rule.UNKNOWN_LABEL]


def test_a_label_holding_cypher_is_rejected_as_unknown(graph_test_batch):
    batch = _with_node(graph_test_batch, NodeRecord(
        "Organization`) DETACH DELETE n //", {"key": "x", "name": "x"},
        (Evidence(SUPPLY_CHUNK, "TSMC", "stated", TEST_PROMPT),),
    ))
    assert _rules(batch) == [Rule.UNKNOWN_LABEL]


def test_an_unknown_edge_type_is_rejected(graph_test_batch):
    batch = _with_edge(
        graph_test_batch, EdgeRecord("PARTNERS_WITH", TSMC, NVIDIA, edge_evidence(SUPPLY_CHUNK)),
    )
    assert _rules(batch) == [Rule.UNKNOWN_EDGE_TYPE]


def test_a_disallowed_start_label_is_rejected(graph_test_batch):
    batch = _with_edge(
        graph_test_batch, EdgeRecord("SUPPLIES", PERIOD, NVIDIA, edge_evidence(SUPPLY_CHUNK)),
    )
    assert _rules(batch) == [Rule.DISALLOWED_ENDPOINT]


def test_a_disallowed_end_label_is_rejected(graph_test_batch):
    batch = _with_edge(
        graph_test_batch, EdgeRecord("HAS_SEGMENT", NVIDIA, TSMC, edge_evidence(SUPPLY_CHUNK)),
    )
    assert _rules(batch) == [Rule.DISALLOWED_ENDPOINT]


def test_an_endpoint_neither_in_the_batch_nor_the_graph_is_rejected(graph_test_batch):
    samsung = NodeRef.of("Organization", key="org:samsung")
    batch = _with_edge(
        graph_test_batch, EdgeRecord("SUPPLIES", samsung, NVIDIA, edge_evidence(SUPPLY_CHUNK)),
    )
    assert _rules(batch) == [Rule.UNKNOWN_ENDPOINT]
    assert _rules(batch, existing_nodes=frozenset({samsung})) == []


def test_an_endpoint_with_the_wrong_key_properties_is_rejected(graph_test_batch):
    by_name = NodeRef.of("Organization", name="TSMC")
    batch = _with_edge(
        graph_test_batch, EdgeRecord("SUPPLIES", by_name, NVIDIA, edge_evidence(SUPPLY_CHUNK)),
    )
    assert _rules(batch) == [Rule.BAD_REFERENCE]


# ── properties ─────────────────────────────────────────────────────────────────

def test_a_missing_required_node_property_is_rejected(graph_test_batch):
    batch = _replace_node(graph_test_batch, "Organization", lambda n: _drop_prop(n, "name"))
    assert _rules(batch) == [Rule.MISSING_PROPERTY]


def test_a_blank_required_string_counts_as_missing(graph_test_batch):
    batch = _replace_node(graph_test_batch, "Organization", lambda n: _set_props(n, name="  "))
    assert _rules(batch) == [Rule.MISSING_PROPERTY]


def test_a_missing_edge_role_is_rejected(graph_test_batch):
    batch = _replace_edge(graph_test_batch, "HOLDS_ROLE_AT", lambda e: _drop_prop(e, "role"))
    assert _rules(batch) == [Rule.MISSING_PROPERTY]


def test_a_wrongly_typed_property_is_rejected(graph_test_batch):
    batch = _replace_node(
        graph_test_batch, "Filing", lambda n: _set_props(n, filing_date="2026-02-26"),
    )
    assert _rules(batch) == [Rule.WRONG_TYPE]


def test_an_int_is_accepted_for_a_float_property(graph_test_batch):
    batch = _replace_node(graph_test_batch, "MetricValue", lambda n: _set_props(n, value=22))
    assert _rules(batch) == []


def test_a_bool_is_rejected_for_a_float_property(graph_test_batch):
    batch = _replace_node(graph_test_batch, "MetricValue", lambda n: _set_props(n, value=True))
    assert _rules(batch) == [Rule.WRONG_TYPE]


def test_a_non_finite_float_is_rejected(graph_test_batch):
    batch = _replace_node(
        graph_test_batch, "MetricValue", lambda n: _set_props(n, value=float("nan")),
    )
    assert _rules(batch) == [Rule.WRONG_TYPE]


def test_an_optional_property_may_be_left_out_but_is_typed_when_given(graph_test_batch):
    owns = EdgeRecord("OWNS", NVIDIA, TSMC, edge_evidence(SUPPLY_CHUNK))
    assert _rules(_with_edge(graph_test_batch, owns)) == []
    assert _rules(_with_edge(graph_test_batch, _set_props(owns, stake="10%"))) == [
        Rule.WRONG_TYPE
    ]


def test_a_property_outside_the_ontology_is_rejected(graph_test_batch):
    batch = _replace_node(
        graph_test_batch, "Organization", lambda n: _set_props(n, country="Taiwan"),
    )
    assert _rules(batch) == [Rule.UNKNOWN_PROPERTY]


def test_ontology_version_is_set_by_the_write_path_not_the_caller(graph_test_batch):
    batch = _replace_node(
        graph_test_batch, "Company", lambda n: _set_props(n, ontology_version=1),
    )
    assert _rules(batch) == [Rule.RESERVED_PROPERTY]


# ── evidence ───────────────────────────────────────────────────────────────────

def test_an_extracted_node_without_evidence_is_rejected(graph_test_batch):
    batch = _replace_node(graph_test_batch, "Segment", lambda n: replace(n, evidence=()))
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_node_evidence_with_a_blank_span_is_rejected(graph_test_batch):
    batch = _replace_node(
        graph_test_batch, "Segment",
        lambda n: replace(n, evidence=(Evidence(SUPPLY_CHUNK, "", "stated", TEST_PROMPT),)),
    )
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_a_structural_node_with_evidence_is_rejected(graph_test_batch):
    batch = _replace_node(
        graph_test_batch, "Company",
        lambda n: replace(n, evidence=(Evidence(SUPPLY_CHUNK, "TSMC", "stated", TEST_PROMPT),)),
    )
    assert _rules(batch) == [Rule.UNEXPECTED_EVIDENCE]


def test_an_evidenced_by_edge_in_a_batch_is_rejected(graph_test_batch):
    chunk = NodeRef.of("Chunk", chunk_id=SUPPLY_CHUNK)
    batch = _with_edge(
        graph_test_batch,
        EdgeRecord("EVIDENCED_BY", TSMC, chunk, {"evidence_span": QUOTES[SUPPLY_CHUNK]}),
    )
    assert _rules(batch) == [Rule.UNEXPECTED_EVIDENCE]


def test_an_extracted_edge_with_no_chunk_ids_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES",
        lambda e: _set_props(e, chunk_ids=[], evidence_spans=[], confidences=[],
                             extract_prompts=[]),
    )
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_an_extracted_edge_without_evidence_properties_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES",
        lambda e: replace(e, properties={}),
    )
    assert _rules(batch) == [Rule.MISSING_PROPERTY] * 4


def test_spans_of_a_different_length_from_chunk_ids_are_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES",
        lambda e: _set_props(e, evidence_spans=[QUOTES[SUPPLY_CHUNK], "TSMC"]),
    )
    assert _rules(batch) == [Rule.EVIDENCE_LENGTH_MISMATCH]


def test_a_blank_edge_span_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES", lambda e: _set_props(e, evidence_spans=[""]),
    )
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_a_chunk_listed_twice_on_one_edge_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES",
        lambda e: _set_props(e, **edge_evidence(SUPPLY_CHUNK, SUPPLY_CHUNK)),
    )
    assert _rules(batch) == [Rule.DUPLICATE_CHUNK]


def test_a_structural_edge_with_chunk_ids_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "FILED", lambda e: _set_props(e, **edge_evidence(SUPPLY_CHUNK)),
    )
    assert _rules(batch) == [Rule.UNKNOWN_PROPERTY] * 4


def test_an_edge_citing_a_chunk_in_neither_the_batch_nor_the_graph_is_rejected(
    graph_test_batch,
):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES",
        lambda e: _set_props(e, chunk_ids=[UNKNOWN_CHUNK_ID]),
    )
    assert _rules(batch) == [Rule.UNKNOWN_CHUNK]
    assert _rules(batch, existing_chunk_ids=frozenset({UNKNOWN_CHUNK_ID})) == []


def test_node_evidence_citing_an_unknown_chunk_is_rejected(graph_test_batch):
    batch = _replace_node(
        graph_test_batch, "Segment",
        lambda n: replace(
            n, evidence=(Evidence(UNKNOWN_CHUNK_ID, "Compute", "stated", TEST_PROMPT),),
        ),
    )
    assert _rules(batch) == [Rule.UNKNOWN_CHUNK]


def test_a_batch_may_cite_chunks_and_nodes_already_in_the_graph():
    batch = Batch(edges=(EdgeRecord("SUPPLIES", TSMC, NVIDIA, edge_evidence(SUPPLY_CHUNK)),))
    assert _rules(batch) == [Rule.UNKNOWN_ENDPOINT, Rule.UNKNOWN_ENDPOINT, Rule.UNKNOWN_CHUNK]
    assert _rules(
        batch,
        existing_chunk_ids=frozenset({SUPPLY_CHUNK}),
        existing_nodes=frozenset({TSMC, NVIDIA}),
    ) == []


# ── confidence and prompt version (ontology version 2) ─────────────────────────

def _replace_evidence(batch, label, **changes):
    """*batch* with the first *label* node's single piece of evidence changed."""
    return _replace_node(
        batch, label, lambda n: replace(n, evidence=(replace(n.evidence[0], **changes),)),
    )


def test_an_extracted_edge_without_confidences_is_rejected(graph_test_batch):
    batch = _replace_edge(graph_test_batch, "SUPPLIES", lambda e: _drop_prop(e, "confidences"))
    assert _rules(batch) == [Rule.MISSING_PROPERTY]


def test_an_extracted_edge_without_extract_prompts_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES", lambda e: _drop_prop(e, "extract_prompts"),
    )
    assert _rules(batch) == [Rule.MISSING_PROPERTY]


def test_confidences_of_a_different_length_from_chunk_ids_are_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES", lambda e: _set_props(e, confidences=["stated"] * 2),
    )
    assert _rules(batch) == [Rule.EVIDENCE_LENGTH_MISMATCH]


def test_extract_prompts_of_a_different_length_from_chunk_ids_are_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES", lambda e: _set_props(e, extract_prompts=[]),
    )
    assert _rules(batch) == [Rule.EVIDENCE_LENGTH_MISMATCH]


def test_an_edge_confidence_outside_the_enum_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES", lambda e: _set_props(e, confidences=["certain"]),
    )
    assert _rules(batch) == [Rule.DISALLOWED_VALUE]


def test_confidence_is_compared_exactly(graph_test_batch):
    # The extractor lower-cases the model's enum values; the write path takes them as given.
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES", lambda e: _set_props(e, confidences=["Stated"]),
    )
    assert _rules(batch) == [Rule.DISALLOWED_VALUE]


def test_a_node_evidence_confidence_outside_the_enum_is_rejected(graph_test_batch):
    batch = _replace_evidence(graph_test_batch, "Segment", confidence="certain")
    assert _rules(batch) == [Rule.DISALLOWED_VALUE]


def test_node_evidence_without_a_confidence_is_rejected(graph_test_batch):
    batch = _replace_evidence(graph_test_batch, "Segment", confidence=None)
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_a_blank_confidence_on_an_edge_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES", lambda e: _set_props(e, confidences=[" "]),
    )
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_node_evidence_without_an_extract_prompt_is_rejected(graph_test_batch):
    batch = _replace_evidence(graph_test_batch, "Segment", extract_prompt=None)
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_a_blank_prompt_version_on_node_evidence_is_rejected(graph_test_batch):
    batch = _replace_evidence(graph_test_batch, "Segment", extract_prompt="  ")
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_a_blank_prompt_version_on_an_edge_is_rejected(graph_test_batch):
    batch = _replace_edge(
        graph_test_batch, "SUPPLIES", lambda e: _set_props(e, extract_prompts=[""]),
    )
    assert _rules(batch) == [Rule.MISSING_EVIDENCE]


def test_every_confidence_level_is_accepted(graph_test_batch):
    for level in ("stated", "implied", "uncertain"):
        batch = _replace_edge(
            graph_test_batch, "SUPPLIES", lambda e, level=level: _set_props(e, confidences=[level]),
        )
        batch = _replace_evidence(batch, "Segment", confidence=level)
        assert _rules(batch) == [], level


# ── several at once ────────────────────────────────────────────────────────────

def test_every_violation_in_a_batch_is_reported(graph_test_batch):
    batch = _replace_node(graph_test_batch, "Segment", lambda n: replace(n, evidence=()))
    batch = _replace_node(batch, "Filing", lambda n: _set_props(n, filing_date="2026-02-26"))
    batch = _with_node(batch, _samsung())
    batch = _with_edge(batch, EdgeRecord("SUPPLIES", PERIOD, NVIDIA, edge_evidence(EXPORT_CHUNK)))
    batch = _with_edge(batch, EdgeRecord("HAS_SEGMENT", NVIDIA, COMPUTE, {
        "chunk_ids": [CUSTOMERS_CHUNK, UNKNOWN_CHUNK_ID], "evidence_spans": ["22%"],
        "confidences": ["stated", "stated"], "extract_prompts": [TEST_PROMPT, TEST_PROMPT],
    }))

    violations = check_batch(batch)

    assert sorted(v.rule for v in violations) == sorted([
        Rule.WRONG_TYPE, Rule.MISSING_EVIDENCE, Rule.DISALLOWED_ENDPOINT,
        Rule.EVIDENCE_LENGTH_MISMATCH, Rule.UNKNOWN_CHUNK,
    ])
    messages = "\n".join(str(v) for v in violations)
    assert "Filing" in messages and "filing_date" in messages
    assert UNKNOWN_CHUNK_ID in messages
    assert str(FILING) in messages
