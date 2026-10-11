"""`python -m resolve.run` and load_nodes(): files, reruns, cache, guards and exit codes (Step 9).

Candidates files are written in extract.run's line format, with names and
chunk IDs from NVDA run 1 and one AMD filing. Chunk text comes from the
fixture filings; the adjudicator is a counting fake behind the real response
cache, answering `distinct` unless told otherwise.
"""
import hashlib
import json
from contextlib import nullcontext

import psycopg
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ingest.store import ChunkNotFound
from ingest.voyage import BatchEmbedding
from resolve.config import ResolveConfig
from resolve.log import read_log, replay
from resolve.nodes import InputError, load_nodes
from resolve.paths import read_alias_map
from resolve.run import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE, main
from resolve.tracing import PAIR_SPAN, RUN_SPAN
from retrieve.answer_model import AnswerModelError, ApiResponse
from retrieve.tracing import CACHE_HIT_ATTR, COST_ATTR, TEXT_ENV
from tests.extract_fakes import body
from tests.resolve_helpers import AMD_10K, NVDA_10K

DIMENSIONS = 1024


def _evidence(chunk_id, span):
    return [{"chunk_id": chunk_id, "confidence": "stated", "extract_prompt": "extract/v1@00000000",
             "span": span}]


def _node(label, name, ordinal, accession_no=NVDA_10K):
    key = f"{label}:{name.casefold()}"
    return {"kind": "node", "label": label, "item": f"{label}(key={key!r})",
            "properties": {"key": key, "name": name},
            "evidence": _evidence(f"{accession_no}:{ordinal:04d}", name)}


def _metric(concept, ordinal, accession_no=NVDA_10K):
    key = f"{accession_no}:1045810:{concept.casefold()}:FY2026"
    node = {"kind": "node", "label": "MetricValue", "item": f"MetricValue(key={key!r})",
            "properties": {"key": key, "concept": concept, "value": 216.0,
                           "unit": "USD billions", "period": "FY2026"},
            "evidence": _evidence(f"{accession_no}:{ordinal:04d}", concept)}
    edge = {"kind": "edge", "type": "MEASURES", "item": "...",
            "start": {"label": "MetricValue", "key": {"key": key}},
            "end": {"label": "Company", "key": {"cik": "1045810"}},
            "properties": {}}
    return [node, edge]


NVDA_LINES = [
    _node("Organization", "Groq", 30), _node("Organization", "Groq, Inc.", 31),
    _node("Regulation", "GDPR", 50), _node("Regulation", "General Data Protection Regulation", 51),
    *_metric("Revenue", 61), *_metric("Total revenue", 62),
    {"kind": "flag", "chunk_id": f"{NVDA_10K}:0001", "reason": "name_not_in_span", "item": "x"},
]
AMD_LINES = [_node("Organization", "NVIDIA Corporation", 40, AMD_10K),
             _node("Organization", "Groq", 41, AMD_10K)]


def _write(directory, name, lines):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


@pytest.fixture
def candidates(tmp_path):
    directory = tmp_path / "extract"
    _write(directory, f"{NVDA_10K}-run1.jsonl", NVDA_LINES[:2])  # an older run, not read
    _write(directory, f"{NVDA_10K}-run2.jsonl", NVDA_LINES)
    _write(directory, f"{AMD_10K}-run1.jsonl", AMD_LINES)
    return directory


class FakeVoyage:
    """A BatchEmbedder with a vector per text from its hash; counts calls."""

    def __init__(self):
        self.calls = 0

    def embed_batch(self, texts, model, input_type):
        self.calls += 1
        vectors = []
        for text in texts:
            seed = hashlib.sha256(text.encode()).digest()
            vectors.append(tuple((seed[i % 32] - 128) / 128 for i in range(DIMENSIONS)))
        return BatchEmbedding(vectors=tuple(vectors), total_tokens=5 * len(texts))


def _never():
    raise AssertionError("no Voyage client should be opened")


class FakeClaude:
    """Answers every pair `distinct` (or *answer*), quoting each node's first chunk."""

    def __init__(self, answer="distinct", error=None):
        self.calls = 0
        self._answer = answer
        self._error = error

    def complete(self, request):
        self.calls += 1
        if self._error is not None:
            raise self._error
        reply = {"answer": self._answer, "related_kind": None, "quote_a": "x",
                 "chunk_id_a": "c", "quote_b": "y", "chunk_id_b": "c", "reason": "fake"}
        return ApiResponse(body=body(reply), api_ms=1.0)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real-0001")
    monkeypatch.setenv("DATABASE_URL", "postgresql://not-used")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


@pytest.fixture(scope="module")
def chunk_texts(test_graph_chunks):
    return {chunk_id: text for chunk_id, (_, text) in test_graph_chunks.items()}


@pytest.fixture
def claude():
    return FakeClaude()


@pytest.fixture
def run_main(tmp_path, candidates, chunk_texts, claude):
    """main() with fakes for Voyage, Postgres, Claude and tracing; keyword overrides."""
    def run(argv=(), **overrides):
        kwargs = {"candidates_dir": candidates, "out_dir": tmp_path / "out",
                  "embed_cache": tmp_path / "cache", "open_client": _never,
                  "config": ResolveConfig(top_k=0), "open_model": lambda: nullcontext(claude),
                  "read_texts": lambda url, ids: {i: chunk_texts[i] for i in ids},
                  "response_cache": tmp_path / "responses", "tracing": lambda environ: None,
                  **overrides}
        return main(list(argv), **kwargs)
    return run


def _report(tmp_path, n):
    return json.loads((tmp_path / "out" / f"run{n}.json").read_text(encoding="utf-8"))


def test_a_run_writes_the_log_the_alias_map_and_the_report(run_main, tmp_path, candidates):
    assert run_main() == EXIT_OK
    out = tmp_path / "out"
    aliases = read_alias_map(out / "alias_map.json")
    assert aliases == replay(read_log(out / "merge_log.jsonl"))
    assert aliases["Organization:groq, inc."] == "Organization:groq"
    assert aliases["Organization:nvidia corporation"] == "Company:1045810"
    report = _report(tmp_path, 1)
    assert [i["file"] for i in report["provenance"]["inputs"]] == [
        f"{AMD_10K}-run1.jsonl", f"{NVDA_10K}-run2.jsonl"]
    assert report["provenance"]["config_hash"] == ResolveConfig(top_k=0).config_hash
    assert report["counts"]["merges"] == {"proposed": 2, "new": 2, "by_rule": {
        "filer_table": 1, "normalized_equal": 1}}
    assert report["counts"]["pending_by_label"] == {}
    adjudication = report["adjudication"]
    assert adjudication["pairs"] == 2 and adjudication["answers"] == {"distinct": 2}
    assert adjudication["model"] == "claude-haiku-5-5" and adjudication["effort"] == "high"
    assert adjudication["prompt_version"].startswith("resolve/v2@")
    assert adjudication["price_table"]["date"] == "2026-10-10"
    assert report["provenance"]["status"] == "complete"
    assert report["labels_and_scoring"]["tuning"] == {"drawn": None, "labelled": 0, "same": 0}


def test_the_run_report_carries_the_tuning_choices_and_the_frozen_settings(run_main, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    tuning = {"rules": [{"name": "chosen", "config_hash": "c" * 64, "pending": 9,
                         "matches_found": 2, "matches": 2, "non_matches_flagged": 1,
                         "non_matches": 3, "missed": []}],
              "adjudicator": {"prompt_version": "resolve/v2@00000000", "answered": 3,
                              "agreed": 3, "cost_usd": 0.01}}
    (out / "tuning-report.json").write_text(json.dumps(tuning), encoding="utf-8")
    (out / "frozen.json").write_text(json.dumps({"config_hash": "c" * 64, "prompts": {
        "label": "label/v2@00000000", "resolve": "resolve/v2@00000000"}}), encoding="utf-8")
    assert run_main() == EXIT_OK
    recorded = _report(tmp_path, 1)["labels_and_scoring"]["tuning_report"]
    assert recorded["chosen"]["pending"] == 9
    assert recorded["adjudicator"] == {"prompt_version": "resolve/v2@00000000", "answered": 3,
                                       "agreed": 3}
    assert recorded["frozen"]["config_hash"] == "c" * 64
    assert len(recorded["report_sha256"]) == 64


def test_a_rerun_on_unchanged_inputs_appends_nothing(run_main, tmp_path, candidates):
    run_main()
    log = tmp_path / "out" / "merge_log.jsonl"
    before = log.read_bytes()
    assert run_main() == EXIT_OK
    assert log.read_bytes() == before
    assert _report(tmp_path, 2)["counts"]["merges"]["new"] == 0


def test_explicit_candidates_files_are_read_instead(run_main, tmp_path, candidates):
    only = candidates / f"{AMD_10K}-run1.jsonl"
    assert run_main(["--candidates", str(only)]) == EXIT_OK
    assert [i["file"] for i in _report(tmp_path, 1)["provenance"]["inputs"]] == [only.name]


def test_a_cached_run_makes_zero_calls(run_main, tmp_path, candidates):
    voyage = FakeVoyage()
    config = ResolveConfig()
    assert run_main(open_client=lambda: nullcontext(voyage), config=config) == EXIT_OK
    assert voyage.calls == 1
    assert _report(tmp_path, 1)["embedding"]["calls"] == 1
    assert run_main(config=config) == EXIT_OK  # _never would raise
    assert _report(tmp_path, 2)["embedding"] == {**_report(tmp_path, 2)["embedding"],
                                                 "calls": 0, "tokens": 0, "cost_usd": 0.0}


def test_uncached_embeddings_without_a_key_cannot_run(run_main, tmp_path, monkeypatch,
                                                      capsys):
    from resolve.run import _voyage_client
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert run_main(open_client=_voyage_client, config=ResolveConfig()) == EXIT_RUN_ERROR
    assert "VOYAGE_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_no_candidates_files_is_bad_input(run_main, tmp_path):
    assert run_main(candidates_dir=tmp_path / "empty") == EXIT_USAGE


def test_bad_arguments_are_bad_input(run_main, tmp_path, candidates):
    assert run_main(["--nonsense"]) == EXIT_USAGE


def test_a_malformed_candidates_file_is_bad_input(run_main, tmp_path, candidates, capsys):
    (candidates / f"{AMD_10K}-run1.jsonl").write_text('{"kind": "node"}\n', encoding="utf-8")
    assert run_main() == EXIT_USAGE
    assert "line 1" in capsys.readouterr().err


def test_an_alias_map_edited_by_hand_fails_the_check(run_main, tmp_path, candidates):
    run_main()
    path = tmp_path / "out" / "alias_map.json"
    path.write_text(json.dumps({"Organization:groq": "Organization:broadcom"}), encoding="utf-8")
    assert run_main() == EXIT_CHECK_FAILED


def test_a_malformed_merge_log_is_bad_input(run_main, tmp_path, candidates):
    out = tmp_path / "out"
    out.mkdir()
    (out / "merge_log.jsonl").write_text("not json\n", encoding="utf-8")
    assert run_main() == EXIT_USAGE


# ── Adjudication: guards, cost cap, cache, failures and tracing ───────────────

@pytest.mark.parametrize("missing", ["ANTHROPIC_API_KEY", "DATABASE_URL"])
def test_a_missing_setting_cannot_run(run_main, monkeypatch, capsys, missing, claude):
    monkeypatch.delenv(missing)
    assert run_main() == EXIT_RUN_ERROR
    assert missing in capsys.readouterr().err
    assert claude.calls == 0


def test_a_base_url_is_refused(run_main, monkeypatch, capsys, claude):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    assert run_main() == EXIT_RUN_ERROR
    assert "ANTHROPIC_BASE_URL" in capsys.readouterr().err
    assert claude.calls == 0


def test_an_estimate_over_the_cap_makes_no_call(run_main, tmp_path, capsys, claude):
    assert run_main(["--max-cost", "0.001"]) == EXIT_RUN_ERROR
    err = capsys.readouterr()
    assert "estimate" in err.out and "--max-cost" in err.err
    assert claude.calls == 0
    assert not (tmp_path / "out").exists()


def test_a_cached_adjudication_makes_zero_calls(run_main, tmp_path, claude):
    assert run_main() == EXIT_OK
    assert claude.calls == 2
    assert run_main(["--max-cost", "0"]) == EXIT_OK  # every call is cached, so $0 is enough
    assert claude.calls == 2
    report = _report(tmp_path, 2)["adjudication"]
    assert report["cache_hits"] == 2 and report["calls_made"] == 0
    assert report["estimate"]["calls"] == 0
    assert report["cost_this_run_usd"] == 0.0 and report["cost_replayed_usd"] > 0


def test_a_failed_call_appends_nothing_and_cannot_finish(run_main, tmp_path, capsys):
    broken = FakeClaude(error=AnswerModelError("Anthropic API call failed (HTTP 529)"))
    assert run_main(open_model=lambda: nullcontext(broken)) == EXIT_RUN_ERROR
    assert not (tmp_path / "out" / "merge_log.jsonl").exists()
    report = _report(tmp_path, 1)
    assert report["provenance"]["status"] == "incomplete"
    assert report["adjudication"]["not_merged_by_reason"] == {"call_failed": 2}
    assert "Rerun" in capsys.readouterr().err
    assert run_main() == EXIT_OK  # the rerun adjudicates and logs
    assert (tmp_path / "out" / "merge_log.jsonl").exists()


def test_an_unreachable_database_cannot_run(run_main, capsys):
    def down(url, ids):
        raise psycopg.OperationalError("connection to postgresql://user:secret@host failed")
    assert run_main(read_texts=down) == EXIT_RUN_ERROR
    err = capsys.readouterr().err
    assert "OperationalError" in err and "secret" not in err


def test_a_chunk_missing_from_postgres_cannot_run(run_main, capsys):
    def missing(url, ids):
        raise ChunkNotFound(ids[0])
    assert run_main(read_texts=missing) == EXIT_RUN_ERROR
    assert "ingest.load" in capsys.readouterr().err


def _provider():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_adjudicator_calls_are_traced_without_text(run_main):
    provider, exporter = _provider()
    assert run_main(tracing=lambda environ: provider) == EXIT_OK
    spans = exporter.get_finished_spans()
    pairs = [s for s in spans if s.name == PAIR_SPAN]
    (root,) = [s for s in spans if s.name == RUN_SPAN]
    assert len(pairs) == 2
    assert all(s.parent.span_id == root.context.span_id for s in pairs)
    attributes = pairs[0].attributes
    assert attributes["llm.model_name"] == "claude-haiku-5-5"
    assert attributes[CACHE_HIT_ATTR] is False and attributes[COST_ATTR] > 0
    assert attributes["llm.token_count.completion"] > 0
    assert attributes["llm.prompt_template.version"].startswith("resolve/v2@")
    assert not any(k.startswith("llm.input_messages") for k in attributes)


def test_prompt_text_goes_on_spans_only_when_asked(run_main, monkeypatch):
    monkeypatch.setenv(TEXT_ENV, "true")
    provider, exporter = _provider()
    assert run_main(tracing=lambda environ: provider) == EXIT_OK
    pair = next(s for s in exporter.get_finished_spans() if s.name == PAIR_SPAN)
    assert pair.attributes["llm.input_messages.1.message.content"].startswith("Label: ")


# ── load_nodes ────────────────────────────────────────────────────────────────

def test_a_key_in_two_files_is_one_node_with_both_evidence(tmp_path, candidates):
    nodes, files = load_nodes(sorted(candidates.glob("*run1.jsonl")) +
                              [candidates / f"{NVDA_10K}-run2.jsonl"])
    (groq,) = [n for n in nodes if n.key == "Organization:groq"]
    assert groq.chunk_ids == (f"{AMD_10K}:0041", f"{NVDA_10K}:0030")
    assert len(files) == 3


def test_a_metric_value_takes_its_subject_from_its_measures_edge(tmp_path, candidates):
    nodes, _ = load_nodes([candidates / f"{NVDA_10K}-run2.jsonl"])
    (revenue,) = [n for n in nodes if n.name == "Revenue"]
    assert revenue.metric.subject_key == "1045810"
    assert revenue.metric.accession_no == NVDA_10K


def test_a_metric_value_without_a_measures_edge_is_skipped_and_listed(tmp_path):
    # User decision 2026-10-11: the extractor kept two Intel MetricValues whose
    # MEASURES edge it dropped; with no subject they cannot pair, so they are skipped.
    orphan = _metric("Revenue", 61)[0]
    path = _write(tmp_path, f"{NVDA_10K}-run1.jsonl", [orphan, *_metric("Net income", 62)])
    nodes, (file,) = load_nodes([path])
    assert [n.name for n in nodes] == ["Net income"]
    assert file.skipped_metrics == (orphan["properties"]["key"],)


def test_the_run_report_lists_skipped_metric_values(run_main, tmp_path, candidates):
    orphan = _metric("Revenue", 61, AMD_10K)[0]
    _write(candidates, f"{AMD_10K}-run2.jsonl", [*AMD_LINES, orphan])
    assert run_main() == EXIT_OK
    inputs = _report(tmp_path, 1)["provenance"]["inputs"]
    assert {i["file"]: i["skipped_metric_values"] for i in inputs} == {
        f"{AMD_10K}-run2.jsonl": [orphan["properties"]["key"]], f"{NVDA_10K}-run2.jsonl": []}


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(InputError, match="cannot read"):
        load_nodes([tmp_path / "absent.jsonl"])
