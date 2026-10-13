"""Merging one filing's chunk outputs, and the conflicts merging finds (Step 8).

The same node or edge from several chunks becomes one record with one
evidence entry per chunk. A single-valued property that differs takes the
most confident value (stated > implied > uncertain), ties to the later
chunk, and each disagreement is a conflict holding both values.
"""
import pytest

from extract.check import CheckedNode, ChunkCheck
from extract.keys import metric_key
from extract.merge import merge
from extract.pipeline import extract_filing
from extract.prompt import load_prompt
from extract.units import UnknownUnit
from graph.batch import Evidence, NodeRef
from tests.extract_fakes import (
    NVDA_FILING,
    FakeExtractModel,
    node,
    triple,
)
from tests.graph_test_data import NVDA_10K

BUSINESS = f"{NVDA_10K}:0006"
SEGMENT_NOTE = f"{NVDA_10K}:0123"
PLATFORM = f"{NVDA_10K}:0005"
EXPORT_RISK = f"{NVDA_10K}:0041"
SEGMENT_SPAN = "The Compute & Networking segment includes our Data Center accelerated computing"
COMPUTE = NodeRef.of("Segment", key="Segment:1045810:compute & networking")
MELLANOX = NodeRef.of("Organization", key="Organization:mellanox")
NVIDIA = NodeRef.of("Company", cik="1045810")


def _segment_reply(name="Compute & Networking", confidence="stated"):
    return {
        "nodes": [node("n1", "Segment", name, SEGMENT_SPAN, confidence)],
        "triples": [triple("FILER", "HAS_SEGMENT", "n1", SEGMENT_SPAN, confidence)],
    }


def _run(nvda_chunks, replies):
    chunks = [nvda_chunks[c] for c in replies]
    return extract_filing(NVDA_FILING, chunks, FakeExtractModel(replies), load_prompt())


def _segment_run(nvda_chunks, first, second):
    return _run(nvda_chunks, {BUSINESS: _segment_reply(*first),
                              SEGMENT_NOTE: _segment_reply(*second)})


def test_one_node_and_one_edge_with_evidence_from_each_chunk(nvda_chunks):
    run = _segment_run(nvda_chunks, (), ())
    [segment] = run.nodes
    assert [e.chunk_id for e in segment.evidence] == [BUSINESS, SEGMENT_NOTE]
    [edge] = run.edges
    assert (edge.type, edge.start, edge.end) == ("HAS_SEGMENT", NVIDIA, COMPUTE)
    assert edge.properties["chunk_ids"] == (BUSINESS, SEGMENT_NOTE)
    assert len(edge.properties["evidence_spans"]) == 2
    assert run.conflicts == () and run.violations == ()


@pytest.mark.parametrize(("first", "second", "kept"), [
    (("Compute & Networking", "stated"), ("COMPUTE & NETWORKING", "implied"),
     "Compute & Networking"),
    (("Compute & Networking", "implied"), ("COMPUTE & NETWORKING", "stated"),
     "COMPUTE & NETWORKING"),
    (("Compute & Networking", "stated"), ("COMPUTE & NETWORKING", "stated"),
     "COMPUTE & NETWORKING"),
    (("Compute & Networking", "uncertain"), ("COMPUTE & NETWORKING", "implied"),
     "COMPUTE & NETWORKING"),
])
def test_the_most_confident_value_wins_and_ties_go_to_the_later_chunk(
    nvda_chunks, first, second, kept,
):
    run = _segment_run(nvda_chunks, first, second)
    [segment] = run.nodes
    assert segment.properties["name"] == kept
    [conflict] = run.conflicts
    assert conflict.property == "name"
    assert conflict.kept == kept
    assert {conflict.kept, conflict.other} == {first[0], second[0]}
    assert {conflict.kept_chunk, conflict.other_chunk} == {BUSINESS, SEGMENT_NOTE}
    assert run.violations == ()


def test_each_evidence_entry_keeps_its_own_confidence(nvda_chunks):
    run = _segment_run(nvda_chunks, ("Compute & Networking", "implied"),
                       ("Compute & Networking", "stated"))
    [edge] = run.edges
    assert edge.properties["confidences"] == ("implied", "stated")
    assert run.conflicts == ()


def test_edge_property_conflicts_are_counted(nvda_chunks):
    def owns(span, stake, confidence):
        return {
            "nodes": [node("n1", "Organization", "Mellanox", span)],
            "triples": [triple("FILER", "OWNS", "n1", span, confidence, stake=stake)],
        }
    run = _run(nvda_chunks, {
        PLATFORM: owns("Our acquisition of Mellanox in 2020", 1.0, "implied"),
        EXPORT_RISK: owns("our Mellanox acquisition", 0.5, "stated"),
    })
    [edge] = run.edges
    assert (edge.start, edge.end) == (NVIDIA, MELLANOX)
    assert edge.properties["stake"] == 0.5
    [conflict] = run.conflicts
    assert (conflict.property, conflict.kept, conflict.other) == ("stake", 0.5, 1.0)
    assert (conflict.kept_chunk, conflict.other_chunk) == (EXPORT_RISK, PLATFORM)


def test_competes_with_merges_in_either_direction(nvda_chunks):
    span = "We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Limited"
    supply = f"{NVDA_10K}:0012"
    reply = {
        "nodes": [node("n1", "Organization", "Taiwan Semiconductor Manufacturing Company "
                       "Limited", span)],
        "triples": [triple("n1", "COMPETES_WITH", "FILER", span),
                    triple("FILER", "COMPETES_WITH", "n1", span, "implied")],
    }
    run = _run(nvda_chunks, {supply: reply})
    [edge] = run.edges
    assert edge.properties["chunk_ids"] == (supply,)
    assert edge.properties["confidences"] == ("stated",)  # one entry per chunk, most confident


def test_one_chunk_naming_an_item_twice_gives_one_entry_and_no_conflict(nvda_chunks):
    reply = _segment_reply()
    reply["nodes"].append(node("n2", "Segment", "COMPUTE & NETWORKING", SEGMENT_SPAN, "implied"))
    run = _run(nvda_chunks, {BUSINESS: reply})
    [segment] = run.nodes
    assert segment.properties["name"] == "Compute & Networking"
    assert [e.confidence for e in segment.evidence] == ["stated"]
    assert run.conflicts == ()


def test_a_recorded_reply_merges_with_another_chunk(nvda_chunks):
    import json

    from tests.anthropic_fixtures import FIXTURES
    recorded = json.loads((FIXTURES / "extract_segment_note.json").read_text(encoding="utf-8"))
    run = _run(nvda_chunks, {BUSINESS: _segment_reply(), SEGMENT_NOTE: recorded["response"]})
    [segment] = [n for n in run.nodes if n.properties["key"] == COMPUTE.key_dict()["key"]]
    assert [e.chunk_id for e in segment.evidence] == [BUSINESS, SEGMENT_NOTE]
    [edge] = [e for e in run.edges if e.type == "HAS_SEGMENT" and e.end == COMPUTE]
    assert edge.properties["chunk_ids"] == (BUSINESS, SEGMENT_NOTE)
    assert run.violations == ()


# ── metric values: unit class in the key, values compared across scales (Step 10) ──

NET_INCOME_USD = f"{NVDA_10K}:0063"
NET_INCOME_PERCENT = f"{NVDA_10K}:0066"
TAX_PROSE = f"{NVDA_10K}:0069"
TAX_TABLE = f"{NVDA_10K}:0088"
TAX_PROSE_SPAN = ("Income tax expense was $21\\.4 billion and $11\\.1 billion for fiscal years "
                  "2026 and 2025, respectively\\.")
TAX_TABLE_SPAN = "Income tax expense | 21,383 |  | 11,146 |  | 4,058"
TAX_KEY = f"{NVDA_10K}:1045810:income tax expense:currency:FY2026"


def _metric_reply(concept, span, value, unit, confidence="stated", period="FY2026"):
    return {
        "nodes": [node("m1", "MetricValue", concept, span, confidence, value=value, unit=unit,
                       period=period)],
        "triples": [triple("THIS_FILING", "REPORTS", "m1", span, confidence),
                    triple("m1", "MEASURES", "FILER", span, confidence)],
    }


def _metric(run, key):
    [found] = [n for n in run.nodes if n.properties["key"] == key]
    return found


def test_a_percentage_and_an_amount_with_one_concept_are_two_metric_values(nvda_chunks):
    run = _run(nvda_chunks, {
        NET_INCOME_USD: _metric_reply("Net income", "Net income | $ | 120,067 |", 120067,
                                      "USD millions"),
        NET_INCOME_PERCENT: _metric_reply("Net income", "Net income | 55.6 | % |", 55.6,
                                          "percent"),
    })
    usd = _metric(run, f"{NVDA_10K}:1045810:net income:currency:FY2026")
    percent = _metric(run, f"{NVDA_10K}:1045810:net income:percent:FY2026")
    assert (usd.properties["value"], usd.properties["unit"]) == (120067.0, "USD millions")
    assert (percent.properties["value"], percent.properties["unit"]) == (55.6, "percent")
    assert len([e for e in run.edges if e.type == "MEASURES"]) == 2
    assert run.conflicts == () and run.violations == ()


def test_one_figure_at_two_scales_keeps_the_more_precise_entry_without_a_conflict(nvda_chunks):
    run = _run(nvda_chunks, {
        TAX_PROSE: _metric_reply("Income tax expense", TAX_PROSE_SPAN, 21.4, "USD billions"),
        TAX_TABLE: _metric_reply("Income tax expense", TAX_TABLE_SPAN, 21383, "USD millions",
                                 "implied"),
    })
    tax = _metric(run, TAX_KEY)
    # the less confident table entry wins: it is the more precise one
    assert (tax.properties["value"], tax.properties["unit"]) == (21383.0, "USD millions")
    assert [e.chunk_id for e in tax.evidence] == [TAX_PROSE, TAX_TABLE]
    assert run.conflicts == () and run.violations == ()


def test_values_that_disagree_across_scales_are_a_conflict(nvda_chunks):
    run = _run(nvda_chunks, {
        TAX_PROSE: _metric_reply("Income tax expense", TAX_PROSE_SPAN, 21.4, "USD billions"),
        TAX_TABLE: _metric_reply("Income tax expense", TAX_TABLE_SPAN, 11146, "USD millions",
                                 "implied"),
    })
    tax = _metric(run, TAX_KEY)
    assert (tax.properties["value"], tax.properties["unit"]) == (21.4, "USD billions")
    assert {(c.property, c.kept, c.other, c.kept_chunk, c.other_chunk) for c in run.conflicts} == {
        ("value", 21.4, 11146.0, TAX_PROSE, TAX_TABLE),
        ("unit", "USD billions", "USD millions", TAX_PROSE, TAX_TABLE),
    }


def test_values_that_disagree_at_one_scale_follow_the_step_8_rule(nvda_chunks):
    run = _run(nvda_chunks, {
        TAX_TABLE: _metric_reply("Income tax expense", TAX_TABLE_SPAN, 21383, "USD millions"),
        f"{NVDA_10K}:0118": _metric_reply("Income tax expense",
                                          "Income tax expense | $ | 21,383 |", 21384,
                                          "USD millions"),
    })
    tax = _metric(run, TAX_KEY)
    assert tax.properties["value"] == 21384.0  # equally confident: the later chunk
    [conflict] = run.conflicts
    assert (conflict.property, conflict.kept, conflict.other) == ("value", 21384.0, 21383.0)


# AMD 10-Q for the quarter ended March 30, 2024 (FY2024-Q1): net revenue printed
# in a table in millions and in the MD&A in billions. That filing is not a test
# fixture, so its real chunk IDs and spans, copied from that filing's extract
# file (data/extract/0000002488-24-000056-run1.jsonl), are merged directly.
AMD_Q1 = "0000002488-24-000056"
AMD_TABLE = f"{AMD_Q1}:0002"
AMD_PROSE = f"{AMD_Q1}:0021"
AMD_PROMPT = "extract/v2@34a82e2b"


def _amd_revenue(chunk_id, span, value, unit, confidence="stated"):
    key = metric_key(AMD_Q1, "2488", "Net revenue", unit, "FY2024-Q1")
    properties = {"key": key, "concept": "Net revenue", "value": value, "unit": unit,
                  "period": "FY2024-Q1"}
    return ChunkCheck(chunk_id, (CheckedNode(
        NodeRef.of("MetricValue", key=key), "MetricValue", properties,
        Evidence(chunk_id, span, confidence, AMD_PROMPT)),), (), (), (), 1)


@pytest.mark.parametrize("prose_first", [True, False])
def test_amd_net_revenue_in_billions_and_millions_is_one_figure(prose_first):
    table = _amd_revenue(AMD_TABLE, "Net revenue |  | $ | 5,473 | $ | 5,353 |", 5473.0,
                         "USD millions")
    prose = _amd_revenue(AMD_PROSE, "Net revenue for the three months ended March 30, 2024 "
                         "was $5\\.5 billion", 5.5, "USD billions")
    merged = merge([prose, table] if prose_first else [table, prose])
    [revenue] = merged.nodes
    assert (revenue.properties["value"], revenue.properties["unit"]) == (5473.0, "USD millions")
    assert merged.conflicts == ()


def test_an_unknown_unit_stops_the_extraction_with_its_name(nvda_chunks):
    with pytest.raises(UnknownUnit, match="'USD trillions'"):
        _run(nvda_chunks, {TAX_PROSE: _metric_reply("Income tax expense", TAX_PROSE_SPAN,
                                                    0.0214, "USD trillions")})
