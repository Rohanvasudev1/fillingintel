"""`python -m extract.run`: guards, cost cap, files, exit codes, cache and tracing (Step 8).

The database tests load four chunks of the NVDA FY2026 10-K fixture into a
throwaway schema and answer them with the real Sonnet replies recorded for
ticket 02, served by a counting fake behind the real response cache.
"""
import json
from contextlib import nullcontext
from dataclasses import replace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from extract.pipeline import extract_filing
from extract.prompt import load_prompt
from extract.run import EXIT_INCOMPLETE, EXIT_OK, EXIT_RUN_ERROR, main
from graph.batch import Violation
from graph.ontology import ONTOLOGY_VERSION
from ingest.chunker import chunk_filing
from ingest.parsed_files import ParsedRecord
from ingest.store import load_filing
from retrieve.answer_model import AnswerModelError, ApiResponse
from retrieve.tracing import TEXT_ENV
from tests.anthropic_fixtures import FIXTURES
from tests.conftest import throwaway_schema
from tests.graph_test_data import NVDA_10K

RECORDED = ("extract_risk_factor", "extract_segment_note", "extract_suppliers",
            "extract_statement_table")
KEY = "sk-ant-not-real-0001"


class CountingModel:
    """Returns the recorded body for each chunk, or raises for chunks in *failing*."""

    def __init__(self, bodies, failing=()):
        self._bodies = bodies
        self._failing = set(failing)
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if request.chunk_id in self._failing:
            raise AnswerModelError("Anthropic API call failed: InternalServerError (HTTP 500)")
        return ApiResponse(body=self._bodies[request.chunk_id], api_ms=1.0)


@pytest.fixture(scope="module")
def recorded():
    return {r["chunk_id"]: r["response"]
            for r in (json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                      for name in RECORDED)}


@pytest.fixture(scope="module")
def conn(nvda_10k_meta, nvda_10k_filing, recorded):
    with throwaway_schema() as connection:
        chunks = tuple(c for c in chunk_filing(nvda_10k_filing) if c.chunk_id in recorded)
        record = ParsedRecord(meta=nvda_10k_meta, filing=nvda_10k_filing, parser_commit="test")
        load_filing(connection, record, chunks)
        yield connection


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test@127.0.0.1:1/unused")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv(TEXT_ENV, raising=False)
    return monkeypatch


def _main(tmp_path, model, conn=None, argv=(), **kwargs):
    kwargs.setdefault("tracing", lambda environ: None)
    if conn is not None:
        kwargs.setdefault("connect", lambda url: nullcontext(conn))
    return main(["--accession", NVDA_10K, *argv],
                open_model=lambda: nullcontext(model),
                candidates_dir=tmp_path / "extract",
                reports_dir=tmp_path / "reports",
                response_cache=tmp_path / "responses",
                **kwargs)


def _report(tmp_path, run=1):
    return json.loads((tmp_path / "reports" / f"{NVDA_10K}-run{run}.json").read_text())


# ── Arguments and guards ──────────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [[], ["--accession", "nvda"],
                                  ["--accession", NVDA_10K, "--max-cost", "-1"]])
def test_bad_arguments_exit_2(argv, env):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


@pytest.mark.parametrize("change", ["no_key", "no_database", "base_url"])
def test_a_missing_setting_or_a_base_url_stops_before_any_call(change, env, tmp_path, capsys):
    if change == "no_key":
        env.delenv("ANTHROPIC_API_KEY")
    elif change == "no_database":
        env.delenv("DATABASE_URL")
    else:
        env.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    model = CountingModel({})
    assert _main(tmp_path, model) == EXIT_RUN_ERROR
    assert model.calls == 0
    assert not (tmp_path / "reports").exists()
    assert KEY not in capsys.readouterr().err


def test_an_unreachable_database_exits_3_without_showing_the_url(env, tmp_path, capsys):
    assert _main(tmp_path, CountingModel({})) == EXIT_RUN_ERROR
    err = capsys.readouterr().err
    assert "database" in err
    assert "127.0.0.1:1" not in err


def test_an_unknown_accession_exits_3(env, conn, tmp_path, capsys):
    code = main(["--accession", "0000000000-00-000000"],
                open_model=lambda: nullcontext(CountingModel({})),
                connect=lambda url: nullcontext(conn), candidates_dir=tmp_path / "extract",
                reports_dir=tmp_path / "reports", response_cache=tmp_path / "responses",
                tracing=lambda environ: None)
    assert code == EXIT_RUN_ERROR
    assert "ingest.load" in capsys.readouterr().err


def test_an_estimate_over_the_cap_exits_3_before_any_call(env, conn, recorded, tmp_path,
                                                          capsys):
    model = CountingModel(recorded)
    assert _main(tmp_path, model, conn, ["--max-cost", "0.001"]) == EXIT_RUN_ERROR
    assert model.calls == 0
    assert not (tmp_path / "reports").exists()
    assert "--max-cost" in capsys.readouterr().err


# ── A complete run ────────────────────────────────────────────────────────────

def test_a_complete_run_writes_candidates_and_a_report_and_exits_0(env, conn, recorded,
                                                                     tmp_path, capsys):
    model = CountingModel(recorded)
    assert _main(tmp_path, model, conn) == EXIT_OK
    assert model.calls == len(recorded)
    assert "estimate" in capsys.readouterr().out
    report = _report(tmp_path)
    prompt = load_prompt()
    assert report["accession_no"] == NVDA_10K
    assert report["run"] == 1
    assert report["status"] == "complete"
    assert report["prompt"]["version"] == prompt.version
    assert report["prompt"]["sha256"] == prompt.sha256
    assert report["prompt"]["prompt_version"] == prompt.prompt_version
    assert (report["model"], report["effort"]) == ("claude-sonnet-5-5", "high")
    assert report["ontology_version"] == ONTOLOGY_VERSION
    assert report["chunk_count"] == len(recorded)
    assert report["failed_chunks"] == []
    assert report["violations"] == []
    assert report["candidates"] > 0
    assert set(report["tokens"]) == {"input", "cache_read", "cache_write", "output"}
    assert report["tokens"]["output"] > 0
    assert report["cost_usd"] > 0
    assert report["price_table"]["date"] == "2026-10-09"
    assert report["commit"]
    assert {"rejections", "flags", "conflicts"} <= set(report)

    lines = [json.loads(line) for line in
             (tmp_path / "extract" / f"{NVDA_10K}-run1.jsonl").read_text().splitlines()]
    kinds = [line["kind"] for line in lines]
    assert kinds.count("node") == report["nodes"] > 0
    assert kinds.count("edge") == report["edges"] > 0
    edge = next(line for line in lines if line["kind"] == "edge")
    assert set(edge) >= {"type", "start", "end", "properties"}
    assert len(edge["properties"]["chunk_ids"]) == len(edge["properties"]["confidences"])


def test_rejections_are_counted_by_reason_with_examples(env, conn, recorded, tmp_path):
    chunk_id = next(iter(recorded))
    bad = {**recorded}
    body = json.loads(json.dumps(recorded[chunk_id]))
    reply = {"nodes": [{"id": "n1", "label": "Planet", "name": "Mars", "value": None,
                        "unit": None, "period": None, "evidence_span": "Mars",
                        "confidence": "stated"}], "triples": []}
    for block in body["content"]:
        if block["type"] == "text":
            block["text"] = json.dumps(reply)
    bad[chunk_id] = body
    assert _main(tmp_path, CountingModel(bad), conn) == EXIT_OK
    rejections = _report(tmp_path)["rejections"]
    assert rejections["by_reason"] == {"unknown_label": 1}
    assert rejections["examples"]["unknown_label"][0]["chunk_id"] == chunk_id


# ── Incomplete runs ───────────────────────────────────────────────────────────

def test_a_failed_chunk_exits_4_and_the_report_says_incomplete(env, conn, recorded, tmp_path):
    failing = sorted(recorded)[1]
    assert _main(tmp_path, CountingModel(recorded, failing=[failing]), conn) == EXIT_INCOMPLETE
    report = _report(tmp_path)
    assert report["status"] == "incomplete"
    assert [f["chunk_id"] for f in report["failed_chunks"]] == [failing]


def test_a_batch_check_violation_exits_4(env, conn, recorded, tmp_path):
    def extract_with_a_bug(*args, **kwargs):
        run = extract_filing(*args, **kwargs)
        return replace(run, violations=(Violation("Product(key='x')", "required", "bug"),))

    code = _main(tmp_path, CountingModel(recorded), conn, extract=extract_with_a_bug)
    assert code == EXIT_INCOMPLETE
    report = _report(tmp_path)
    assert report["status"] == "incomplete"
    assert report["violations"] == ["Product(key='x'): required: bug"]


# ── Cache ─────────────────────────────────────────────────────────────────────

def test_a_rerun_with_the_same_prompt_makes_no_calls(env, conn, recorded, tmp_path):
    model = CountingModel(recorded)
    assert _main(tmp_path, model, conn) == EXIT_OK
    first = _report(tmp_path)
    assert _main(tmp_path, model, conn) == EXIT_OK
    assert model.calls == len(recorded)  # all from the first run
    second = _report(tmp_path, run=2)
    assert second["calls"] == {"made": 0, "from_cache": len(recorded)}
    assert second["estimate"]["calls"] == 0
    assert second["cost_this_run_usd"] == 0
    assert second["cost_usd"] == pytest.approx(first["cost_usd"])
    assert second["nodes"] == first["nodes"]


# ── Tracing ───────────────────────────────────────────────────────────────────

def _traced(env, conn, recorded, tmp_path):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    assert _main(tmp_path, CountingModel(recorded), conn,
                 tracing=lambda environ: provider) == EXIT_OK
    spans = exporter.get_finished_spans()
    (root,) = [s for s in spans if s.parent is None]
    return root, [s for s in spans if s is not root]


def test_each_chunk_call_is_an_llm_span_under_the_run(env, conn, recorded, tmp_path):
    root, chunks = _traced(env, conn, recorded, tmp_path)
    assert root.attributes["openinference.span.kind"] == "CHAIN"
    assert len(chunks) == len(recorded)
    assert {s.attributes["filingintel.chunk_id"] for s in chunks} == set(recorded)
    for span in chunks:
        assert span.parent.span_id == root.context.span_id
        assert span.attributes["openinference.span.kind"] == "LLM"
        assert span.attributes["llm.model_name"] == "claude-sonnet-5-5"
        assert span.attributes["filingintel.cache_hit"] is False
        assert span.attributes["filingintel.cost_usd"] > 0
        assert span.attributes["llm.token_count.completion"] > 0
        assert not any(k.startswith(("llm.input_messages", "llm.output_messages"))
                       for k in span.attributes)
        assert KEY not in json.dumps(dict(span.attributes))


def test_chunk_and_reply_text_go_on_spans_only_when_text_capture_is_on(env, conn, recorded,
                                                                      tmp_path):
    env.setenv(TEXT_ENV, "true")
    _, chunks = _traced(env, conn, recorded, tmp_path)
    for span in chunks:
        assert span.attributes["llm.input_messages.1.message.role"] == "user"
        assert span.attributes["llm.input_messages.1.message.content"]
        assert span.attributes["llm.output_messages.0.message.content"]


# ── Review fixes ──────────────────────────────────────────────────────────────

def test_chunk_texts_are_what_resolve_returns(conn, recorded):
    from ingest.store import filing_chunk_texts, resolve

    texts = filing_chunk_texts(conn, NVDA_10K)
    assert [t.chunk_id for t in texts] == sorted(recorded)
    assert all(t.text == resolve(conn, t.chunk_id) for t in texts)


def _all_uncertain(body):
    """*body* with every node and triple in its reply marked `uncertain`, so each is flagged."""
    body = json.loads(json.dumps(body))
    for block in body["content"]:
        if block["type"] == "text":
            reply = json.loads(block["text"])
            for item in (*reply["nodes"], *reply["triples"]):
                item["confidence"] = "uncertain"
            block["text"] = json.dumps(reply)
    return body


def test_candidate_lines_name_their_item_as_flags_do(env, conn, recorded, tmp_path):
    uncertain = {chunk_id: _all_uncertain(body) for chunk_id, body in recorded.items()}
    assert _main(tmp_path, CountingModel(uncertain), conn) == EXIT_OK
    lines = [json.loads(line) for line in
             (tmp_path / "extract" / f"{NVDA_10K}-run1.jsonl").read_text().splitlines()]
    items = {line["item"] for line in lines if line["kind"] in ("node", "edge")}
    edge = next(line for line in lines if line["kind"] == "edge")
    assert f"-[{edge['type']}]->" in edge["item"]
    flags = [line for line in lines if line["kind"] == "flag"]
    assert {flag["item"] for flag in flags} == items


def test_the_report_gives_the_rejection_rate(env, conn, recorded, tmp_path):
    assert _main(tmp_path, CountingModel(recorded), conn) == EXIT_OK
    rejections = _report(tmp_path)["rejections"]
    candidates = _report(tmp_path)["candidates"]
    assert rejections["rate"] == pytest.approx(rejections["total"] / candidates)


def test_a_failed_report_write_leaves_no_candidates_file(env, conn, recorded, tmp_path):
    (tmp_path / "reports").write_text("a file where the folder should be")
    assert _main(tmp_path, CountingModel(recorded), conn) == EXIT_RUN_ERROR
    assert not list((tmp_path / "extract").glob("*.jsonl"))


def test_a_model_without_a_price_exits_3_before_any_call(env, conn, recorded, tmp_path,
                                                         monkeypatch, capsys):
    from retrieve.pricing import PriceTable

    monkeypatch.setattr("extract.run.EXTRACTION_PRICES", PriceTable("2000-01-01", {}))
    model = CountingModel(recorded)
    assert _main(tmp_path, model, conn) == EXIT_RUN_ERROR
    assert model.calls == 0
    assert "no price" in capsys.readouterr().err
