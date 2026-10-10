"""extract_filing() on real Sonnet replies to prompt v2, replayed offline (Step 9).

Two chunks recorded once by scripts/capture_extract_responses.py: NVIDIA's
FY2026 10-K segment note, which names a reportable segment and the
revenue-by-end-market table (0125), and the NVDA 10-Q's revenue-by-market-
platform note (0032). Replayed, they must give Segment and MarketPlatform
nodes keyed by NVIDIA's CIK and pass check_batch().
"""
import json

import pytest

from extract.filers import FilingInfo
from extract.pipeline import extract_filing
from extract.prompt import load_prompt
from extract.request import ChunkInput
from graph.batch import NodeRef
from ingest.chunker import chunk_filing
from retrieve.answer_model import ApiResponse
from tests.anthropic_fixtures import FIXTURES
from tests.extract_fakes import NVDA_FILING
from tests.graph_test_data import NVDA_10K

NVDA_CIK = "1045810"
NVIDIA = NodeRef.of("Company", cik=NVDA_CIK)
TEN_K_NOTE = "extract_v2_segment_note"
TEN_Q_NOTE = "extract_v2_10q_market_platforms"


class _Replay:
    def __init__(self, bodies):
        self._bodies = bodies

    def complete(self, request):
        return ApiResponse(body=self._bodies[request.chunk_id], api_ms=1.0, from_cache=True)


def _recorded(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _run(filing, chunks, name):
    recorded = _recorded(name)
    chunk = chunks[recorded["chunk_id"]]
    return extract_filing(filing, [chunk], _Replay({chunk.chunk_id: recorded["response"]}),
                          load_prompt())


@pytest.fixture(scope="module")
def ten_q_filing(nvda_10q_meta):
    m = nvda_10q_meta
    return FilingInfo(cik=m.cik, accession_no=m.accession_no, company_name=m.company_name,
                      form_type=m.form_type, fiscal_period=m.fiscal_period)


@pytest.fixture(scope="module")
def ten_q_chunks(nvda_10q_filing):
    return {c.chunk_id: ChunkInput(c.chunk_id, c.section,
                                   nvda_10q_filing.text[c.char_start:c.char_end])
            for c in chunk_filing(nvda_10q_filing)}


@pytest.fixture(scope="module")
def ten_k_run(nvda_chunks):
    return _run(NVDA_FILING, nvda_chunks, TEN_K_NOTE)


@pytest.fixture(scope="module")
def ten_q_run(ten_q_filing, ten_q_chunks):
    return _run(ten_q_filing, ten_q_chunks, TEN_Q_NOTE)


def _keys(run, label):
    return {n.properties["key"] for n in run.nodes if n.label == label}


def _metric_subjects(run):
    return {e.end for e in run.edges if e.type == "MEASURES"}


def test_recorded_with_the_current_prompt():
    for name in (TEN_K_NOTE, TEN_Q_NOTE):
        assert _recorded(name)["prompt_version"] == load_prompt().prompt_version


def test_the_10q_reply_is_for_a_10q_chunk(ten_q_filing):
    assert ten_q_filing.form_type == "10-Q"
    assert _recorded(TEN_Q_NOTE)["chunk_id"].startswith(ten_q_filing.accession_no)
    assert _recorded(TEN_K_NOTE)["chunk_id"].startswith(NVDA_10K)


@pytest.mark.parametrize("which", ["ten_k_run", "ten_q_run"])
def test_each_reply_completes_and_passes_the_batch_check(which, request):
    run = request.getfixturevalue(which)
    assert run.failures == ()
    assert run.violations == ()
    assert run.complete


def test_the_segment_note_gives_a_segment_and_market_platforms_keyed_by_cik(ten_k_run):
    assert f"Segment:{NVDA_CIK}:compute & networking" in _keys(ten_k_run, "Segment")
    platforms = _keys(ten_k_run, "MarketPlatform")
    for name in ("data center", "gaming", "professional visualization", "automotive"):
        assert f"MarketPlatform:{NVDA_CIK}:{name}" in platforms, name


def test_no_market_platform_is_labelled_a_segment(ten_k_run, ten_q_run):
    for run in (ten_k_run, ten_q_run):
        segments = _keys(run, "Segment")
        assert not {f"Segment:{NVDA_CIK}:{name}" for name in ("data center", "gaming")} & segments


def test_the_10q_note_gives_market_platforms_keyed_by_cik(ten_q_run):
    assert f"MarketPlatform:{NVDA_CIK}:data center" in _keys(ten_q_run, "MarketPlatform")


@pytest.mark.parametrize("which", ["ten_k_run", "ten_q_run"])
def test_a_figure_for_a_market_platform_carries_its_key(which, request):
    run = request.getfixturevalue(which)
    accession_no = run.accession_no
    platform_subjects = {s for s in _metric_subjects(run) if s.label == "MarketPlatform"}
    assert platform_subjects
    metrics = _keys(run, "MetricValue")
    for subject in platform_subjects:
        prefix = f"{accession_no}:{subject.key_dict()['key']}:"
        assert any(k.startswith(prefix) for k in metrics), subject


@pytest.mark.parametrize("which", ["ten_k_run", "ten_q_run"])
def test_every_span_is_a_slice_of_its_chunk(which, request, nvda_chunks, ten_q_chunks):
    run = request.getfixturevalue(which)
    chunks = {**nvda_chunks, **ten_q_chunks}
    for n in run.nodes:
        for e in n.evidence:
            assert e.span in chunks[e.chunk_id].text
