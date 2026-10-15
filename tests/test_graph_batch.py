"""Tests for the pure batch check (Step 7).

Each test starts from the shared valid test graph and breaks one thing, so it
shows one rule on its own. No database.
"""
from dataclasses import replace

from graph.batch import Batch, EdgeRecord, Evidence, NodeRecord, NodeRef, Rule, check_batch
from tests.graph_test_data import (
    AMD,
    AMD_COMPETITION_CHUNK,
    COMPUTE,
    CUSTOMERS_CHUNK,
    DATA_CENTER,
    EXPORT_CHUNK,
    FILING,
    HUANG,
    NVDA_10K,
    NVIDIA,
    OFFICERS_CHUNK,
    PERIOD,
    QUOTES,
    SEGMENTS_CHUNK,
    SUPPLY_CHUNK,
    SUPPLY_COMMUNITY,
    TEST_PROMPT,
    TOP_CUSTOMER_SHARE,
    TSMC,
    edge_evidence,
    with_communities,
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


# ── ontology version 3: market platforms, filer-scoped keys, aliases ──────────────

def test_a_market_platform_as_the_end_of_has_segment_is_rejected(graph_test_batch):
    batch = _with_edge(graph_test_batch, EdgeRecord(
        "HAS_SEGMENT", NVIDIA, DATA_CENTER, edge_evidence(SEGMENTS_CHUNK)))
    assert _rules(batch) == [Rule.DISALLOWED_ENDPOINT]


def test_affects_to_an_unlisted_label_is_rejected(graph_test_batch):
    risk = NodeRef.of("RiskFactor", key=f"{NVDA_10K}:export controls")
    batch = _with_edge(graph_test_batch, EdgeRecord(
        "AFFECTS", risk, TSMC, edge_evidence(EXPORT_CHUNK)))
    violations = check_batch(batch, existing_nodes=frozenset({risk}))
    assert [v.rule for v in violations] == [Rule.DISALLOWED_ENDPOINT]
    assert "end label 'Organization'" in violations[0].message


def test_a_segment_key_without_a_cik_is_rejected(graph_test_batch):
    batch = _replace_node(graph_test_batch, "Segment",
                          lambda n: _set_props(n, key="Segment:compute & networking"))
    assert _rules(batch, existing_nodes=frozenset({COMPUTE})) == [Rule.BAD_KEY]


def test_a_market_platform_key_under_another_label_is_rejected(graph_test_batch):
    batch = _replace_node(graph_test_batch, "MarketPlatform",
                          lambda n: _set_props(n, key="Segment:1045810:data center"))
    assert _rules(batch, existing_nodes=frozenset({DATA_CENTER})) == [Rule.BAD_KEY]


def test_other_labels_keep_their_own_key_formats(graph_test_batch):
    batch = _replace_node(graph_test_batch, "Organization",
                          lambda n: _set_props(n, key="Organization:1045810:tsmc"))
    assert _rules(batch, existing_nodes=frozenset({TSMC})) == []


def test_aliases_that_are_not_a_list_of_strings_are_rejected(graph_test_batch):
    for bad in ("TSMC", ["TSMC", 3]):
        batch = _replace_node(graph_test_batch, "Organization",
                              lambda n, bad=bad: _set_props(n, aliases=bad))
        assert _rules(batch) == [Rule.WRONG_TYPE], bad


def test_aliases_may_be_left_out_or_empty(graph_test_batch):
    left_out = _replace_node(graph_test_batch, "Organization", lambda n: _drop_prop(n, "aliases"))
    empty = _replace_node(graph_test_batch, "Organization", lambda n: _set_props(n, aliases=[]))
    assert _rules(left_out) == [] and _rules(empty) == []


def test_aliases_are_not_defined_for_per_filing_labels(graph_test_batch):
    batch = _replace_node(graph_test_batch, "MetricValue",
                          lambda n: _set_props(n, aliases=["top customer share"]))
    assert _rules(batch) == [Rule.UNKNOWN_PROPERTY]


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


# ── duplicate items (Step 10) ──────────────────────────────────────────────────

def test_the_same_node_key_twice_is_rejected_naming_the_node(graph_test_batch):
    tsmc = next(n for n in graph_test_batch.nodes if n.label == "Organization")
    batch = _with_node(graph_test_batch, _set_props(tsmc, name="TSMC"))

    violations = check_batch(batch)

    assert [v.rule for v in violations] == [Rule.DUPLICATE_ITEM]
    assert str(TSMC) in str(violations[0])


def test_the_same_edge_twice_is_rejected_naming_the_edge(graph_test_batch):
    batch = _with_edge(graph_test_batch, EdgeRecord("HAS_SEGMENT", NVIDIA, COMPUTE,
                                                    edge_evidence(SEGMENTS_CHUNK)))

    violations = check_batch(batch)

    assert [v.rule for v in violations] == [Rule.DUPLICATE_ITEM]
    assert "HAS_SEGMENT" in violations[0].item and str(COMPUTE) in violations[0].item


def test_the_same_edge_with_other_evidence_is_still_a_duplicate(graph_test_batch):
    batch = _with_edge(graph_test_batch, EdgeRecord("SUPPLIES", TSMC, NVIDIA,
                                                    edge_evidence(EXPORT_CHUNK)))
    assert _rules(batch) == [Rule.DUPLICATE_ITEM]


def test_the_same_structural_edge_twice_is_rejected(graph_test_batch):
    batch = _with_edge(graph_test_batch, EdgeRecord("FILED", NVIDIA, FILING))
    assert _rules(batch) == [Rule.DUPLICATE_ITEM]


def test_one_role_twice_is_a_duplicate_but_two_roles_are_not(graph_test_batch):
    # The test graph already holds Huang as President and as Chief Executive Officer.
    assert _rules(graph_test_batch) == []
    batch = _with_edge(graph_test_batch, EdgeRecord(
        "HOLDS_ROLE_AT", HUANG, NVIDIA, {"role": "President", **edge_evidence(OFFICERS_CHUNK)},
    ))
    assert _rules(batch) == [Rule.DUPLICATE_ITEM]


def test_the_same_edge_type_between_other_nodes_is_not_a_duplicate(graph_test_batch):
    batch = _with_edge(graph_test_batch, EdgeRecord("COMPETES_WITH", NVIDIA, AMD,
                                                    edge_evidence(AMD_COMPETITION_CHUNK)))
    assert _rules(batch) == []


def test_a_duplicate_with_its_own_violations_reports_both(graph_test_batch):
    batch = _with_edge(graph_test_batch, EdgeRecord("SUPPLIES", TSMC, NVIDIA, {
        **edge_evidence(SUPPLY_CHUNK), "confidences": ["certain"],
    }))
    assert sorted(_rules(batch)) == sorted([Rule.DUPLICATE_ITEM, Rule.DISALLOWED_VALUE])


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
        # The second HAS_SEGMENT edge repeats the test graph's own (Step 10).
        Rule.DUPLICATE_ITEM,
    ])
    messages = "\n".join(str(v) for v in violations)
    assert "Filing" in messages and "filing_date" in messages
    assert UNKNOWN_CHUNK_ID in messages
    assert str(FILING) in messages


# ── communities (ontology version 5) ─────────────────────────────────────────

def test_communities_and_their_edges_have_no_violations(graph_test_batch):
    assert check_batch(with_communities(graph_test_batch)) == ()


def test_a_community_with_evidence_is_rejected(graph_test_batch):
    batch = _replace_node(
        with_communities(graph_test_batch), "Community",
        lambda n: replace(n, evidence=(Evidence(SUPPLY_CHUNK, "TSMC", "stated", TEST_PROMPT),)),
    )
    assert _rules(batch) == [Rule.UNEXPECTED_EVIDENCE]


def test_a_metric_value_cannot_be_a_community_member(graph_test_batch):
    batch = _with_edge(with_communities(graph_test_batch),
                       EdgeRecord("IN_COMMUNITY", TOP_CUSTOMER_SHARE, SUPPLY_COMMUNITY))
    assert _rules(batch) == [Rule.DISALLOWED_ENDPOINT]


def test_a_community_level_outside_top_and_lower_is_rejected(graph_test_batch):
    batch = _replace_node(with_communities(graph_test_batch), "Community",
                          lambda n: _set_props(n, level="middle"))
    assert _rules(batch) == [Rule.DISALLOWED_VALUE]


def test_derived_edges_take_no_evidence_lists(graph_test_batch):
    batch = _replace_edge(with_communities(graph_test_batch), "PARENT_OF",
                          lambda e: _set_props(e, **edge_evidence(SUPPLY_CHUNK)))
    assert set(_rules(batch)) == {Rule.UNKNOWN_PROPERTY}
