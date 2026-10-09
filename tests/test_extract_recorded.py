"""extract_filing() on real Sonnet replies, replayed offline (Step 8).

Four chunks of the NVDA FY2026 10-K fixture, recorded once by
scripts/capture_extract_responses.py: a risk factor (0029), the segment note
(0123), the Business paragraph naming suppliers (0012) and a statement table
(0089). The replies are what the model really returned for prompt v1.
"""
import json

import pytest

from extract.pipeline import extract_filing
from extract.prompt import load_prompt
from graph.batch import NodeRef
from retrieve.answer_model import ApiResponse
from tests.anthropic_fixtures import FIXTURES
from tests.extract_fakes import NVDA_FILING
from tests.graph_test_data import NVDA_10K

RECORDED = ("extract_risk_factor", "extract_segment_note", "extract_suppliers",
            "extract_statement_table")
NVIDIA = NodeRef.of("Company", cik="1045810")
AMD = NodeRef.of("Company", cik="2488")
INTEL = NodeRef.of("Company", cik="50863")
FILING = NodeRef.of("Filing", accession_no=NVDA_10K)


class _Replay:
    def __init__(self, bodies):
        self._bodies = bodies

    def complete(self, request):
        return ApiResponse(body=self._bodies[request.chunk_id], api_ms=1.0, from_cache=True)


@pytest.fixture(scope="module")
def recorded():
    return [json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
            for name in RECORDED]


@pytest.fixture(scope="module")
def run(recorded, nvda_chunks):
    bodies = {r["chunk_id"]: r["response"] for r in recorded}
    chunks = [nvda_chunks[r["chunk_id"]] for r in recorded]
    return extract_filing(NVDA_FILING, chunks, _Replay(bodies), load_prompt())


def _node(run, label, key):
    return next(n for n in run.nodes if n.label == label and n.properties["key"] == key)


def _edges(run, edge_type):
    return {(e.start, e.end) for e in run.edges if e.type == edge_type}


def test_recorded_with_the_current_prompt(recorded):
    assert {r["prompt_version"] for r in recorded} == {load_prompt().prompt_version}


def test_every_chunk_completes_and_passes_the_batch_check(run):
    assert run.failures == ()
    assert run.violations == ()
    assert run.complete
    assert all(c.candidates > 0 for c in run.chunks)


def test_filer_references_map_to_ciks_and_the_accession_number(run):
    tsmc = NodeRef.of("Organization",
                      key="Organization:taiwan semiconductor manufacturing company limited")
    assert (tsmc, NVIDIA) in _edges(run, "SUPPLIES")
    competitors = {pair for pair in _edges(run, "COMPETES_WITH")}
    assert {(NVIDIA, AMD), (NVIDIA, INTEL)} <= competitors
    assert all(start == FILING for start, _ in _edges(run, "REPORTS"))
    assert all(start == FILING for start, _ in _edges(run, "DISCLOSES"))


def test_keys_follow_the_rules(run):
    samsung = _node(run, "Organization", "Organization:samsung electronics co., ltd.")
    assert samsung.properties["name"] == "Samsung Electronics Co., Ltd."
    segment_revenue = _node(
        run, "MetricValue", f"{NVDA_10K}:Segment:compute & networking:revenue:FY2026")
    assert (segment_revenue.properties["value"], segment_revenue.properties["unit"]) == (
        193479.0, "USD millions")
    net_income = _node(run, "MetricValue", f"{NVDA_10K}:1045810:net income:FY2026")
    assert net_income.properties["value"] == 120067.0
    [risk] = [n for n in run.nodes if n.label == "RiskFactor"]
    assert risk.properties["key"].startswith(f"{NVDA_10K}:product, system security")
    assert "\\" not in risk.properties["title"]


def test_every_span_is_a_slice_of_its_chunk(run, nvda_chunks):
    for n in run.nodes:
        for e in n.evidence:
            assert e.span in nvda_chunks[e.chunk_id].text
    for edge in run.edges:
        for chunk_id, span in zip(edge.properties["chunk_ids"],
                                  edge.properties["evidence_spans"], strict=True):
            assert span in nvda_chunks[chunk_id].text


def test_usage_comes_from_the_recorded_replies(run, recorded):
    output = sum(r["response"]["usage"]["output_tokens"] for r in recorded)
    assert run.usage.output_tokens == output
    assert all(c.from_cache for c in run.chunks)
