"""Merging one filing's chunk outputs, and the conflicts merging finds (Step 8).

The same node or edge from several chunks becomes one record with one
evidence entry per chunk. A single-valued property that differs takes the
most confident value (stated > implied > uncertain), ties to the later
chunk, and each disagreement is a conflict holding both values.
"""
import pytest

from extract.pipeline import extract_filing
from extract.prompt import load_prompt
from graph.batch import NodeRef
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
COMPUTE = NodeRef.of("Segment", key="Segment:compute & networking")
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
