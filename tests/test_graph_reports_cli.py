"""`python -m graph.reports`: estimate, refusals and the run file (Step 12).

The inputs are a hand-built batch around the real chunk IDs and quotes of
tests/graph_test_data.py, with the NVIDIA FY2026 10-K fixture's chunk texts,
and its communities found by the real code. The model replays a recorded
report body whose text is rewritten to cite those chunks.
"""
import json
from contextlib import nullcontext

import pytest

from graph import reports
from graph.batch import Batch, EdgeRecord, Evidence, NodeRecord, NodeRef
from graph.community import find_communities
from graph.community_graph import community_graph
from retrieve.answer_model import AnswerModelError, ApiResponse
from tests.anthropic_fixtures import with_text
from tests.graph_test_data import (
    EXPORT_CHUNK,
    QUOTES,
    SUPPLY_CHUNK,
    TEST_PROMPT,
    edge_evidence,
)
from tests.report_helpers import LOWER_IDS, load_report


def _node(label, key, *chunk_ids):
    evidence = tuple(Evidence(c, QUOTES[c], "stated", TEST_PROMPT) for c in chunk_ids)
    return NodeRecord(label, {"key": key, "name": key.split(":")[-1]}, evidence)


TSMC = _node("Organization", "Organization:tsmc", SUPPLY_CHUNK, EXPORT_CHUNK)
BLACKWELL = _node("Product", "Product:blackwell", SUPPLY_CHUNK)
EXPORT = _node("Regulation", "Regulation:export controls", EXPORT_CHUNK)
BATCH = Batch(nodes=(TSMC, BLACKWELL, EXPORT), edges=(
    EdgeRecord("SUBJECT_TO", NodeRef.of("Product", key="Product:blackwell"),
               NodeRef.of("Regulation", key="Regulation:export controls"),
               edge_evidence(EXPORT_CHUNK)),))
REPLY = {"title": "TSMC and export controls",
         "summary": f"NVIDIA uses TSMC as a foundry [{SUPPLY_CHUNK}]. Unsupported claim.",
         "findings": [{"text": f"Export controls apply [{EXPORT_CHUNK}]."}]}


class FakeModel:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def complete(self, request):
        self.calls += 1
        if self.fail:
            raise AnswerModelError("API down")
        body = with_text(load_report(LOWER_IDS[0])["response"], json.dumps(REPLY))
        return ApiResponse(body=body, api_ms=5.0)


@pytest.fixture
def texts(nvda_10k_filing):
    from ingest.chunker import chunk_filing

    return {c.chunk_id: nvda_10k_filing.text[c.char_start:c.char_end]
            for c in chunk_filing(nvda_10k_filing)}


@pytest.fixture
def setup(tmp_path, texts, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    graph = community_graph(BATCH)
    found = find_communities(graph)
    out = tmp_path / "communities"
    out.mkdir()
    (out / "communities-run1.json").write_text(json.dumps({
        "community_graph": {"input_sha256": graph.input_sha256},
        "communities": [{"community_id": c.community_id, "level": c.level,
                         "parent": c.parent, "member_hash": c.member_hash,
                         "reportable": c.reportable, "members": list(c.members)}
                        for c in found]}))
    model = FakeModel()

    def run(*argv, model=model):
        return reports.main(list(argv), communities_dir=out, read=lambda: (BATCH, texts),
                            open_model=lambda: nullcontext(model),
                            response_cache=tmp_path / "cache")

    return out, model, run


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_without_send_it_prints_the_estimate_and_sends_and_writes_nothing(setup, capsys):
    out, model, run = setup
    assert run() == 0
    assert "estimate $" in capsys.readouterr().out
    assert model.calls == 0 and not list(out.glob("reports-run*"))


def test_send_writes_the_run_file_with_checked_reports(setup):
    out, model, run = setup
    assert run("--send") == 0
    header, *records = _lines(out / "reports-run1.jsonl")
    assert model.calls == 2 and header["complete"] and header["run"] == 1
    assert [r["level"] for r in records] == ["lower", "top"]
    lower = records[0]
    assert lower["report_chunk_ids"] == sorted([SUPPLY_CHUNK, EXPORT_CHUNK])
    assert lower["dropped"] == 1 and "Unsupported" not in lower["report"]
    assert lower["prompt_version"] == header["prompt"]["version"]
    assert header["totals"]["dropped_sentences"] == 2
    assert header["cost_this_run_usd"] == pytest.approx(sum(r["cost_usd"] for r in records))
    assert header["cost_this_run_usd"] > 0


def test_a_rerun_replays_the_cache_and_costs_nothing(setup):
    out, model, run = setup
    assert run("--send") == 0 and run("--send") == 0
    header, *_ = _lines(out / "reports-run2.jsonl")
    assert model.calls == 2 and header["cost_this_run_usd"] == 0
    assert header["estimate"]["calls"] == 0


def test_it_refuses_an_estimate_over_max_cost(setup, capsys):
    out, model, run = setup
    assert run("--send", "--max-cost", "0") == 3
    assert "over --max-cost" in capsys.readouterr().err
    assert model.calls == 0 and not list(out.glob("reports-run*"))


def test_it_refuses_a_run_that_would_pass_the_step_12_cap(setup, capsys):
    out, model, run = setup
    (out / "reports-run1.jsonl").write_text(json.dumps({"cost_this_run_usd": 5.9999}) + "\n")
    assert run("--send") == 3
    assert "$6.00 cap" in capsys.readouterr().err and model.calls == 0
    assert not (out / "reports-run2.jsonl").exists()


def test_a_failed_call_writes_an_incomplete_file_and_exits_3(setup):
    out, _, run = setup
    assert run("--send", model=FakeModel(fail=True)) == 3
    header, *records = _lines(out / "reports-run1.jsonl")
    assert not header["complete"] and records == []
    assert [f["reason"] for f in header["failures"]] == ["call_failed", "lower_report_failed"]


def test_it_refuses_a_communities_file_from_other_builder_output(setup, capsys):
    out, model, run = setup
    path = out / "communities-run1.json"
    data = json.loads(path.read_text())
    data["community_graph"]["input_sha256"] = "0" * 64
    (out / "communities-run2.json").write_text(json.dumps(data))
    assert run() == 2 and "other builder output" in capsys.readouterr().err


def test_it_needs_a_communities_file(setup, capsys):
    out, _, run = setup
    (out / "communities-run1.json").unlink()
    assert run() == 2 and "graph.communities" in capsys.readouterr().err


def test_it_refuses_while_anthropic_base_url_is_set(setup, monkeypatch, capsys):
    _, model, run = setup
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy.invalid")
    assert run("--send") == 3 and "ANTHROPIC_BASE_URL" in capsys.readouterr().err
    assert model.calls == 0


def test_it_needs_the_api_key(setup, monkeypatch):
    _, _, run = setup
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert run() == 3


def test_bad_arguments_exit_2(setup):
    _, _, run = setup
    assert run("--max-cost", "-1") == 2
