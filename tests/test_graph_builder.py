"""The graph builder: structural nodes, alias mapping and combined evidence (Step 10).

Records are plain; chunk IDs and spans are the real ones of tests/graph_test_data.py.
"""
from dataclasses import replace
from datetime import date

import pytest

from graph.batch import (
    CHUNK_IDS,
    CONFIDENCES,
    EVIDENCE_SPANS,
    EXTRACT_PROMPTS,
    Evidence,
    NodeRef,
    check_batch,
)
from graph.build_input import (
    ChunkRow,
    ExtractEdge,
    ExtractFile,
    ExtractNode,
    FilingRow,
    StructuralRows,
)
from graph.builder import BuildError, DropReason, PromptMix, build_graph
from persist.decisions import LinkEvidence
from persist.links import EXACT_TITLE, PersistLink
from tests.graph_test_data import (
    AMD_10K,
    AMD_COMPETITION_CHUNK,
    EXPORT_CHUNK,
    NVDA_10K,
    QUOTES,
    SUPPLY_CHUNK,
)

PROMPT = "extract/v2@34a82e2b"
EARLIER_10K = "0001045810-25-000023"
EARLIER_CHUNK = f"{EARLIER_10K}:0010"
NVIDIA = NodeRef.of("Company", cik="1045810")
AMD = NodeRef.of("Company", cik="2488")
TSMC = "Organization:tsmc"
TSMC_LONG = "Organization:taiwan semiconductor manufacturing company limited"
XILINX = "Organization:xilinx"


def _rows() -> StructuralRows:
    chunk_ids = [*QUOTES, EARLIER_CHUNK]
    return StructuralRows(
        filings=(
            FilingRow(NVDA_10K, "1045810", "NVIDIA CORP", "10-K", "FY2026", date(2026, 2, 26)),
            FilingRow(EARLIER_10K, "1045810", "NVIDIA CORP", "10-K", "FY2025",
                      date(2025, 2, 26)),
            FilingRow(AMD_10K, "2488", "ADVANCED MICRO DEVICES INC", "10-K", "FY2025",
                      date(2026, 2, 25)),
        ),
        chunks=tuple(ChunkRow(c, c.split(":")[0], "part_i_item_1", 0, 1) for c in chunk_ids),
        risk_sections=(),  # body evidence is tested in test_graph_bodies.py
    )


def _ev(chunk_id, confidence="stated", span=None, prompt=PROMPT):
    return Evidence(chunk_id, span or QUOTES.get(chunk_id, "a span"), confidence, prompt)


def _org(key, name, *evidence):
    return ExtractNode("Organization", {"key": key, "name": name}, tuple(evidence))


def _org_ref(key):
    return NodeRef.of("Organization", key=key)


def _edge_in(type_, start, end, *evidence, role=None, stake=None):
    return ExtractEdge(type_, start, end, role, stake, tuple(evidence))


def _file(accession_no, nodes=(), edges=(), run=1):
    return ExtractFile(accession_no, run, "0" * 64, tuple(nodes), tuple(edges))


def _build(files, alias_map=None, links=()):
    return build_graph(_rows(), files, alias_map or {}, links)


def _node(built, label, key):
    found = [n for n in built.batch.nodes
             if n.label == label and n.properties.get("key") == key]
    assert len(found) == 1, found
    return found[0]


def _edges(built, type_):
    return [e for e in built.batch.edges if e.type == type_]


# ── structural nodes ───────────────────────────────────────────────────────────

def test_structural_nodes_come_from_the_rows_and_chunks_hold_no_text():
    built = _build([])
    labels = [n.label for n in built.batch.nodes]
    assert (labels.count("Company"), labels.count("Filing"), labels.count("Period")) == (2, 3, 3)
    assert labels.count("Chunk") == len(_rows().chunks)
    nvidia = next(n for n in built.batch.nodes if n.label == "Company"
                  and n.properties["cik"] == "1045810")
    assert dict(nvidia.properties) == {"cik": "1045810", "name": "NVIDIA CORP", "ticker": "NVDA"}
    chunk = next(n for n in built.batch.nodes if n.label == "Chunk")
    assert set(chunk.properties) == {"chunk_id", "accession_no", "section"}
    assert len(_edges(built, "PART_OF")) == len(_rows().chunks)
    assert (len(_edges(built, "FILED")), len(_edges(built, "COVERS_PERIOD"))) == (3, 3)
    assert check_batch(built.batch) == ()


def test_a_filing_from_a_company_outside_the_three_filers_is_refused():
    rows = _rows()
    stranger = FilingRow("0000000001-26-000001", "1", "X", "10-K", "FY2026", date(2026, 1, 1))
    with pytest.raises(BuildError, match="0000000001-26-000001"):
        build_graph(replace(rows, filings=(*rows.filings, stranger)), [], {}, ())


# ── alias mapping and merged nodes ─────────────────────────────────────────────

def test_a_key_absent_from_the_alias_map_is_its_own_canonical_key():
    built = _build([_file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK))],
                          [_edge_in("SUPPLIES", _org_ref(TSMC), NVIDIA, _ev(SUPPLY_CHUNK))])])
    node = _node(built, "Organization", TSMC)
    assert dict(node.properties) == {"key": TSMC, "name": "TSMC"}
    assert check_batch(built.batch) == ()


def test_a_merged_node_takes_the_canonical_members_properties_and_sorted_aliases():
    files = [
        _file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK))]),
        _file(EARLIER_10K, [_org(TSMC_LONG, "Taiwan Semiconductor Manufacturing Company Limited",
                                 _ev(EARLIER_CHUNK))]),
        _file(AMD_10K, [_org("Organization:tsmc ltd", "TSMC", _ev(AMD_COMPETITION_CHUNK)),
                        _org("Organization:tsm", "Taiwan Semi", _ev(AMD_COMPETITION_CHUNK))]),
    ]
    alias = {TSMC: TSMC_LONG, "Organization:tsmc ltd": TSMC_LONG,
             "Organization:tsm": TSMC_LONG}
    built = _build(files, alias)
    node = _node(built, "Organization", TSMC_LONG)
    assert node.properties["name"] == "Taiwan Semiconductor Manufacturing Company Limited"
    assert node.properties["aliases"] == ["TSMC", "Taiwan Semi"]
    assert [n.properties["key"] for n in built.batch.nodes
            if n.label == "Organization"] == [TSMC_LONG]


def test_a_merged_node_takes_its_canonical_keys_properties_from_the_latest_filing():
    files = [_file(NVDA_10K, [_org(TSMC, "Taiwan Semiconductor", _ev(SUPPLY_CHUNK))]),
             _file(EARLIER_10K, [_org(TSMC, "TSMC", _ev(EARLIER_CHUNK))])]
    node = _node(_build(files), "Organization", TSMC)
    assert node.properties["name"] == "Taiwan Semiconductor"
    assert node.properties["aliases"] == ["TSMC"]


def test_node_evidence_is_one_entry_per_chunk_in_filing_date_order_most_confident_winning():
    files = [
        _file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK, "implied", "later, implied"))]),
        _file(EARLIER_10K, [_org(TSMC_LONG, "TSMC", _ev(EARLIER_CHUNK),
                                 _ev(SUPPLY_CHUNK, "stated", "earlier, stated"))]),
        _file(AMD_10K, [_org("Organization:tsmc ltd", "TSMC", _ev(AMD_COMPETITION_CHUNK))]),
    ]
    alias = {TSMC: TSMC_LONG, "Organization:tsmc ltd": TSMC_LONG}
    node = _node(_build(files, alias), "Organization", TSMC_LONG)
    # FY2025 10-K (2025-02-26), AMD 10-K (2026-02-25), NVIDIA 10-K (2026-02-26)
    assert [e.chunk_id for e in node.evidence] == [EARLIER_CHUNK, AMD_COMPETITION_CHUNK,
                                                   SUPPLY_CHUNK]
    assert node.evidence[2].span == "earlier, stated"


def test_a_node_merged_into_a_filer_is_its_company_and_its_edges_follow():
    rival = "Organization:nvidia corporation"
    files = [_file(AMD_10K, [_org(rival, "NVIDIA Corporation", _ev(AMD_COMPETITION_CHUNK))],
                   [_edge_in("COMPETES_WITH", AMD, _org_ref(rival), _ev(AMD_COMPETITION_CHUNK))])]
    built = _build(files, {rival: "Company:1045810"})
    assert not [n for n in built.batch.nodes if n.label == "Organization"]
    (edge,) = _edges(built, "COMPETES_WITH")
    assert {edge.start, edge.end} == {AMD, NVIDIA}
    assert built.report.merged_into_company == (rival,)
    assert check_batch(built.batch) == ()


# ── merged edges ───────────────────────────────────────────────────────────────

def test_merged_edge_evidence_is_ordered_by_filing_then_chunk_one_entry_per_chunk():
    files = [
        _file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK))],
              [_edge_in("SUPPLIES", _org_ref(TSMC), NVIDIA,
                        _ev(SUPPLY_CHUNK, "implied", "later"), _ev(EXPORT_CHUNK))]),
        _file(EARLIER_10K, [_org(TSMC_LONG, "TSMC", _ev(EARLIER_CHUNK))],
              [_edge_in("SUPPLIES", _org_ref(TSMC_LONG), NVIDIA,
                        _ev(SUPPLY_CHUNK, "stated", "earlier"), _ev(EARLIER_CHUNK))]),
    ]
    (edge,) = _edges(_build(files, {TSMC: TSMC_LONG}), "SUPPLIES")
    assert list(edge.properties[CHUNK_IDS]) == [EARLIER_CHUNK, SUPPLY_CHUNK, EXPORT_CHUNK]
    assert list(edge.properties[EVIDENCE_SPANS])[1] == "earlier"
    assert list(edge.properties[CONFIDENCES]) == ["stated", "stated", "stated"]


def test_equally_confident_entries_for_one_chunk_go_to_the_later_filing():
    files = [
        _file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK))],
              [_edge_in("SUPPLIES", _org_ref(TSMC), NVIDIA, _ev(SUPPLY_CHUNK, span="later"))]),
        _file(EARLIER_10K, [_org(TSMC_LONG, "TSMC", _ev(EARLIER_CHUNK))],
              [_edge_in("SUPPLIES", _org_ref(TSMC_LONG), NVIDIA,
                        _ev(SUPPLY_CHUNK, span="earlier"))]),
    ]
    for ordered in (files, files[::-1]):
        (edge,) = _edges(_build(ordered, {TSMC: TSMC_LONG}), "SUPPLIES")
        assert list(edge.properties[EVIDENCE_SPANS]) == ["later"]


def _owns(accession_no, chunk, confidence, stake):
    return _file(accession_no, [_org(XILINX, "Xilinx", _ev(chunk))],
                 [_edge_in("OWNS", AMD, _org_ref(XILINX), _ev(chunk, confidence), stake=stake)])


def test_a_differing_stake_takes_the_most_confident_value_and_is_a_conflict():
    built = _build([_owns(NVDA_10K, SUPPLY_CHUNK, "implied", 1.0),
                    _owns(EARLIER_10K, EARLIER_CHUNK, "stated", 0.9)])
    (edge,) = _edges(built, "OWNS")
    assert edge.properties["stake"] == 0.9
    (conflict,) = built.report.conflicts
    assert (conflict.property, conflict.kept, conflict.other) == ("stake", 0.9, 1.0)
    assert (conflict.kept_chunk, conflict.other_chunk) == (EARLIER_CHUNK, SUPPLY_CHUNK)


def test_an_equally_confident_stake_goes_to_the_later_filing():
    built = _build([_owns(NVDA_10K, SUPPLY_CHUNK, "stated", 1.0),
                    _owns(EARLIER_10K, EARLIER_CHUNK, "stated", 0.9)])
    (edge,) = _edges(built, "OWNS")
    assert edge.properties["stake"] == 1.0
    assert len(built.report.conflicts) == 1


def test_a_losing_stake_held_by_two_members_is_one_conflict():
    built = _build([_owns(NVDA_10K, SUPPLY_CHUNK, "stated", 1.0),
                    _owns(EARLIER_10K, EARLIER_CHUNK, "implied", 0.9),
                    _owns(AMD_10K, AMD_COMPETITION_CHUNK, "implied", 0.9)])
    assert [(c.kept, c.other) for c in built.report.conflicts] == [(1.0, 0.9)]


def test_one_person_with_two_roles_keeps_two_edges():
    huang = NodeRef.of("Person", key="Person:jen-hsun huang")
    person = ExtractNode("Person", {"key": huang.key_dict()["key"], "name": "Jen-Hsun Huang"},
                         (_ev(SUPPLY_CHUNK),))
    files = [_file(NVDA_10K, [person], [
        _edge_in("HOLDS_ROLE_AT", huang, NVIDIA, _ev(SUPPLY_CHUNK), role="President"),
        _edge_in("HOLDS_ROLE_AT", huang, NVIDIA, _ev(SUPPLY_CHUNK), role="CEO"),
    ])]
    assert sorted(e.properties["role"] for e in _edges(_build(files), "HOLDS_ROLE_AT")) == [
        "CEO", "President"]


# ── left out and dropped ───────────────────────────────────────────────────────

def test_a_metric_value_without_a_measures_edge_is_left_out_and_listed():
    key = f"{NVDA_10K}:50863:receivable:currency:FY2026"
    metric = ExtractNode("MetricValue", {"key": key, "concept": "receivable", "value": 1.0,
                                         "unit": "USD millions", "period": "FY2026"},
                         (_ev(SUPPLY_CHUNK),))
    reports = _edge_in("REPORTS", NodeRef.of("Filing", accession_no=NVDA_10K),
                       NodeRef.of("MetricValue", key=key), _ev(SUPPLY_CHUNK))
    built = _build([_file(NVDA_10K, [metric], [reports])])
    assert not [n for n in built.batch.nodes if n.label == "MetricValue"]
    assert not _edges(built, "REPORTS")
    assert built.report.skipped_metric_values == (key,)
    assert check_batch(built.batch) == ()


def test_edges_a_merge_makes_impossible_are_dropped_and_listed():
    files = [_file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK)),
                              _org("Organization:nvidia", "NVIDIA", _ev(SUPPLY_CHUNK))], [
        # OWNS may not end at a Company
        _edge_in("OWNS", _org_ref(TSMC), _org_ref("Organization:nvidia"), _ev(SUPPLY_CHUNK)),
        # TSMC and its alias become one node
        _edge_in("COMPETES_WITH", _org_ref(TSMC), _org_ref("Organization:tsmc ltd"),
                 _ev(SUPPLY_CHUNK)),
    ])]
    files[0] = replace(files[0], nodes=(*files[0].nodes,
                                        _org("Organization:tsmc ltd", "TSMC", _ev(SUPPLY_CHUNK))))
    built = _build(files, {"Organization:nvidia": "Company:1045810",
                           "Organization:tsmc ltd": TSMC})
    assert not _edges(built, "OWNS") and not _edges(built, "COMPETES_WITH")
    assert sorted(d.reason for d in built.report.dropped_edges) == [
        DropReason.DISALLOWED_ENDPOINT, DropReason.SELF_LOOP]
    assert check_batch(built.batch) == ()


def test_an_agreement_concerning_an_organization_merged_into_a_filer_is_kept():
    # Ontology 4 (Step 11): Company is a CONCERNS end, so Step 10's five dropped
    # agreement edges load.
    deal = "Agreement:wafer supply agreement"
    agreement = ExtractNode("Agreement", {"key": deal, "name": "wafer supply agreement"},
                            (_ev(SUPPLY_CHUNK),))
    files = [_file(NVDA_10K, [agreement,
                              _org("Organization:nvidia", "NVIDIA", _ev(SUPPLY_CHUNK))], [
        _edge_in("CONCERNS", NodeRef.of("Agreement", key=deal), _org_ref("Organization:nvidia"),
                 _ev(SUPPLY_CHUNK)),
    ])]
    built = _build(files, {"Organization:nvidia": "Company:1045810"})
    [edge] = _edges(built, "CONCERNS")
    assert edge.end == NVIDIA
    assert built.report.dropped_edges == ()
    assert check_batch(built.batch) == ()


def test_an_edge_to_a_node_no_file_holds_is_refused():
    edge = _edge_in("SUPPLIES", _org_ref(TSMC), NVIDIA, _ev(SUPPLY_CHUNK))
    with pytest.raises(BuildError, match="tsmc"):
        _build([_file(NVDA_10K, [], [edge])])


def test_evidence_from_a_chunk_outside_the_rows_is_refused():
    with pytest.raises(BuildError, match="0001045810-26-000021:9999"):
        _build([_file(NVDA_10K, [_org(TSMC, "TSMC", _ev(f"{NVDA_10K}:9999"))])])


# ── prompt versions ────────────────────────────────────────────────────────────

def test_a_mix_of_prompt_versions_is_refused():
    files = [_file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK))]),
             _file(EARLIER_10K, [_org(XILINX, "Xilinx",
                                      _ev(EARLIER_CHUNK, prompt="extract/v2@00000000"))])]
    with pytest.raises(PromptMix):
        _build(files)


def test_files_under_another_prompt_version_are_refused():
    files = [_file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK, prompt="extract/v1@4b83bf9b"))])]
    with pytest.raises(PromptMix, match="extract/v2"):
        _build(files)


# ── persistence links ─────────────────────────────────────────────────────────

EARLIER_RISK = f"{EARLIER_10K}:export controls"
LATER_RISK = f"{NVDA_10K}:export controls"


def _risk(key, chunk_id):
    return ExtractNode("RiskFactor", {"key": key, "title": "Export controls"},
                       (_ev(chunk_id),))


def _risk_files():
    return [_file(EARLIER_10K, [_risk(EARLIER_RISK, EARLIER_CHUNK)]),
            _file(NVDA_10K, [_risk(LATER_RISK, EXPORT_CHUNK)])]


def _link(earlier_key=EARLIER_RISK, later_key=LATER_RISK, later_chunk=EXPORT_CHUNK):
    return PersistLink(earlier_key, later_key, EXACT_TITLE,
                       LinkEvidence(EARLIER_CHUNK, "a span"),
                       LinkEvidence(later_chunk, QUOTES[EXPORT_CHUNK]))


def test_a_persistence_link_is_one_persists_as_edge_with_both_evidence_entries():
    built = _build(_risk_files(), links=[_link()])
    [edge] = _edges(built, "PERSISTS_AS")
    assert (edge.start, edge.end) == (NodeRef.of("RiskFactor", key=EARLIER_RISK),
                                      NodeRef.of("RiskFactor", key=LATER_RISK))
    assert list(edge.properties[CHUNK_IDS]) == [EARLIER_CHUNK, EXPORT_CHUNK]
    assert list(edge.properties[EVIDENCE_SPANS]) == ["a span", QUOTES[EXPORT_CHUNK]]
    assert list(edge.properties[CONFIDENCES]) == ["stated", "stated"]
    assert list(edge.properties[EXTRACT_PROMPTS]) == [EXACT_TITLE, EXACT_TITLE]
    assert check_batch(built.batch) == ()


def test_a_link_naming_a_missing_risk_factor_fails_with_its_keys():
    missing = f"{NVDA_10K}:tariffs"
    with pytest.raises(BuildError, match=f"{EARLIER_RISK}.*{missing}"):
        _build(_risk_files(), links=[_link(later_key=missing)])


def test_a_link_citing_a_chunk_outside_its_risk_factors_evidence_is_refused():
    with pytest.raises(BuildError, match=f"{SUPPLY_CHUNK}.*{LATER_RISK}"):
        _build(_risk_files(), links=[_link(later_chunk=SUPPLY_CHUNK)])


def test_two_links_to_one_later_risk_factor_are_refused():
    other = f"{EARLIER_10K}:tariffs"
    files = [_file(EARLIER_10K, [_risk(EARLIER_RISK, EARLIER_CHUNK), _risk(other, EARLIER_CHUNK)]),
             _file(NVDA_10K, [_risk(LATER_RISK, EXPORT_CHUNK)])]
    with pytest.raises(BuildError, match=f"{LATER_RISK} has two predecessors"):
        _build(files, links=[_link(), _link(earlier_key=other)])


# ── determinism ────────────────────────────────────────────────────────────────

def test_the_batch_does_not_depend_on_the_order_files_are_given():
    files = [
        _file(NVDA_10K, [_org(TSMC, "TSMC", _ev(SUPPLY_CHUNK))],
              [_edge_in("SUPPLIES", _org_ref(TSMC), NVIDIA, _ev(SUPPLY_CHUNK))]),
        _file(EARLIER_10K, [_org(TSMC_LONG, "TSMC", _ev(EARLIER_CHUNK))],
              [_edge_in("SUPPLIES", _org_ref(TSMC_LONG), NVIDIA, _ev(EARLIER_CHUNK))]),
    ]
    alias = {TSMC: TSMC_LONG}
    assert _build(files, alias) == _build(files[::-1], alias)
