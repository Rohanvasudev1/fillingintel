"""A traced eval run: one question through ``eval.run`` with the real vector arm (Step 6).

The arm searches the throwaway schema with the fake query embedder and writes
its answer with the scripted answer model; the judges are a fixed fake.  Each
test hands ``main`` its own provider with an in-memory exporter, so spans are
read back here and never sent anywhere.
"""
import itertools
import json
from contextlib import nullcontext
from datetime import date
from types import MappingProxyType

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from eval.judging.runner import JudgeSpec
from eval.judging.scoring import JudgeConfig, RunScores, metrics_for
from eval.run import EXIT_USAGE, main
from eval.schema import dump_records, load_records
from ingest.provenance import REPO_ROOT
from retrieve.arm import ArmSpec
from retrieve.pricing import embedding_cost
from retrieve.tracing import ENDPOINT_ENV, TEXT_ENV, Kind, SpanRecorder
from retrieve.vector import VectorArm
from tests.vector_fakes import (
    EMBED_TOKENS,
    MODEL,
    FakeQueryEmbedder,
    ScriptedAnswerModel,
    embed_fixture_filings,
)

RECORD = {r.id: r for r in load_records(REPO_ROOT / "eval" / "agent_drafted_set.jsonl")}["q0072"]
TODAY = date(2026, 10, 6)
SECRETS = {"ANTHROPIC_API_KEY": "sk-ant-not-real-0001", "VOYAGE_API_KEY": "pa-not-real-0002",
           "OPENAI_API_KEY": "sk-not-real-0003"}
HEADER_WORDS = ("authorization", "header", "api_key", "api-key", "x-api-key")


class FixedJudge:
    config = JudgeConfig()

    def judge(self, item, run: int) -> RunScores:
        scores = MappingProxyType({m: 0.5 for m in metrics_for(item.class_)})
        return RunScores(run, scores, MappingProxyType({}), 0.0, calls=0, replayed=0)


class SpanningJudge(FixedJudge):
    """A fixed judge that opens one EVALUATOR span per metric on the recorder it was given."""

    def __init__(self, spans: SpanRecorder):
        self._spans = spans

    def judge(self, item, run: int) -> RunScores:
        for metric in metrics_for(item.class_):
            with self._spans.span(metric, Kind.EVALUATOR):
                pass
        return super().judge(item, run)


JUDGES = JudgeSpec(required_env=(), open=lambda cache, spans: nullcontext(FixedJudge()))
SPANNING_JUDGES = JudgeSpec(required_env=(),
                            open=lambda cache, spans: nullcontext(SpanningJudge(spans)))


@pytest.fixture
def embedded(db_conn, fixture_records):
    return embed_fixture_filings(db_conn, fixture_records)


@pytest.fixture
def eval_dir(tmp_path):
    directory = tmp_path / "eval"
    dump_records([RECORD], directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    return directory


def _memory_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _run(db_conn, eval_dir, runs_dir, provider, answer_model=None, judges=JUDGES) -> int:
    def open_arm(response_cache, spans=None):
        ticks = itertools.count()  # the same latencies on every run
        return nullcontext(VectorArm(db_conn, FakeQueryEmbedder(),
                                     answer_model or ScriptedAnswerModel(), MODEL,
                                     clock=lambda: float(next(ticks)), spans=spans))

    spec = ArmSpec(name="vector", required_env=(), open=open_arm)
    return main(["--arm", "vector"], arms={"vector": spec}, judges=judges, eval_dir=eval_dir,
                runs_dir=runs_dir, today=TODAY, response_cache=runs_dir / "responses",
                uncached_root=runs_dir / "uncached", tracing=lambda environ: provider)


def _traced(db_conn, eval_dir, tmp_path, answer_model=None):
    provider, exporter = _memory_provider()
    runs = tmp_path / "runs"
    assert _run(db_conn, eval_dir, runs, provider, answer_model) == 0
    spans = {s.name: s for s in exporter.get_finished_spans()}
    (path,) = runs.glob("*.json")
    return spans, json.loads(path.read_text())


def _kind(span) -> str:
    return span.attributes["openinference.span.kind"]


def test_one_question_is_one_trace_of_five_openinference_spans(db_conn, embedded, eval_dir,
                                                                tmp_path):
    spans, _ = _traced(db_conn, eval_dir, tmp_path)
    assert {name: _kind(s) for name, s in spans.items()} == {
        "answer_question": "CHAIN", "question_filter": "CHAIN", "query_embedding": "EMBEDDING",
        "vector_search": "RETRIEVER", "answer_generation": "LLM",
    }
    root = spans.pop("answer_question")
    assert root.parent is None
    for span in spans.values():
        assert span.parent.span_id == root.context.span_id
        assert span.context.trace_id == root.context.trace_id


def test_judge_spans_sit_under_their_question_after_its_answer_span_ends(
        db_conn, embedded, eval_dir, tmp_path):
    provider, exporter = _memory_provider()
    assert _run(db_conn, eval_dir, tmp_path / "runs", provider, judges=SPANNING_JUDGES) == 0
    finished = exporter.get_finished_spans()
    (root,) = [s for s in finished if s.name == "answer_question"]
    evaluators = [s for s in finished if _kind(s) == "EVALUATOR"]
    assert len(evaluators) == len(metrics_for(RECORD.class_)) * JudgeConfig().runs
    for span in evaluators:
        assert span.parent.span_id == root.context.span_id
        assert span.context.trace_id == root.context.trace_id
        assert span.start_time >= root.end_time  # judging starts after every answer


def test_the_root_span_holds_the_question_its_record_and_the_answer_status(
        db_conn, embedded, eval_dir, tmp_path):
    spans, document = _traced(db_conn, eval_dir, tmp_path)
    root = spans["answer_question"].attributes
    assert root["input.value"] == RECORD.question
    assert json.loads(root["metadata"]) == {"question_id": "q0072", "set": "agent_drafted",
                                            "arm": "vector", "split": "dev", "class": "lookup"}
    (question,) = document["questions"]
    assert root["output.value"] == question["answer"]["status"] == "answered"


def test_retrieval_spans_show_the_filter_the_embedding_and_the_ranked_chunks(
        db_conn, embedded, eval_dir, tmp_path):
    spans, document = _traced(db_conn, eval_dir, tmp_path)
    (question,) = document["questions"]
    assert json.loads(spans["question_filter"].attributes["output.value"]) == question["filter"]
    embed = spans["query_embedding"].attributes
    assert embed["embedding.model_name"] == MODEL
    assert embed["embedding.embeddings.0.embedding.text"] == RECORD.question
    assert embed["filingintel.embedding.token_count"] == EMBED_TOKENS
    assert embed["filingintel.cost_usd"] == pytest.approx(embedding_cost(MODEL, EMBED_TOKENS))
    assert embed["filingintel.cache_hit"] is False
    search = spans["vector_search"].attributes
    listed = [{"chunk_id": search[f"retrieval.documents.{i}.document.id"],
               "score": search[f"retrieval.documents.{i}.document.score"]} for i in range(10)]
    assert listed == question["retrieved"]
    assert "retrieval.documents.10.document.id" not in search
    assert {r["chunk_id"] for r in listed} <= set(embedded)


def test_the_generation_span_carries_model_provider_tokens_and_our_cost(
        db_conn, embedded, eval_dir, tmp_path):
    spans, document = _traced(db_conn, eval_dir, tmp_path)
    llm = spans["answer_generation"].attributes
    assert llm["llm.model_name"] == "claude-sonnet-5-5"
    assert (llm["llm.provider"], llm["llm.system"]) == ("anthropic", "anthropic")
    assert json.loads(llm["llm.invocation_parameters"]) == {"effort": "high", "max_tokens": 16000}
    assert llm["llm.prompt_template.version"] == "v1"
    assert llm["llm.token_count.prompt"] > 0 and llm["llm.token_count.completion"] > 0
    assert llm["filingintel.cache_hit"] is False
    (question,) = document["questions"]
    assert llm["filingintel.cost_usd"] == pytest.approx(question["cost_usd"]["generation"])


def test_a_replayed_answer_carries_no_token_counts(db_conn, embedded, eval_dir, tmp_path):
    spans, _ = _traced(db_conn, eval_dir, tmp_path, ScriptedAnswerModel(cached=True))
    llm = spans["answer_generation"].attributes
    assert not [k for k in llm if k.startswith("llm.token_count.")]
    assert llm["filingintel.cache_hit"] is True


def test_prompt_and_answer_text_stay_off_spans_by_default(db_conn, embedded, eval_dir, tmp_path):
    spans, _ = _traced(db_conn, eval_dir, tmp_path)
    for span in spans.values():
        assert not [k for k in span.attributes if k.startswith(("llm.input_messages",
                                                                "llm.output_messages"))]
    assert "output.value" not in spans["answer_generation"].attributes


def test_prompt_and_answer_text_go_on_the_generation_span_when_switched_on(
        db_conn, embedded, eval_dir, tmp_path, monkeypatch):
    monkeypatch.setenv(TEXT_ENV, "true")
    model = ScriptedAnswerModel()
    spans, document = _traced(db_conn, eval_dir, tmp_path, model)
    llm = spans["answer_generation"].attributes
    (request,) = model.requests
    assert llm["llm.input_messages.0.message.content"] == request.system
    assert llm["llm.input_messages.1.message.content"] == request.user
    (question,) = document["questions"]
    assert llm["output.value"] == question["answer"]["text"]


def test_no_span_holds_an_api_key_the_database_url_or_a_header(
        db_conn, embedded, eval_dir, tmp_path, monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(TEXT_ENV, "true")  # the most text a span can hold
    spans, _ = _traced(db_conn, eval_dir, tmp_path)
    secrets = [*SECRETS.values(), db_conn.info.password or "", db_conn.info.dsn]
    for span in spans.values():
        attributes = [dict(span.attributes), *(dict(e.attributes) for e in span.events)]
        for key, value in ((k, v) for a in attributes for k, v in a.items()):
            assert not any(word in key.lower() for word in HEADER_WORDS), key
            assert not any(s and s in str(value) for s in secrets), key


def test_tracing_changes_no_score_and_no_run_file_field(db_conn, embedded, eval_dir, tmp_path):
    traced = tmp_path / "traced"
    untraced = tmp_path / "untraced"
    provider, exporter = _memory_provider()
    assert _run(db_conn, eval_dir, traced, provider) == 0
    assert _run(db_conn, eval_dir, untraced, None) == 0
    assert exporter.get_finished_spans()  # the traced run did record spans
    documents = []
    for runs in (traced, untraced):
        (path,) = runs.glob("*.json")
        document = json.loads(path.read_text())
        document["header"].pop("created_at")
        documents.append(document)
    assert documents[0] == documents[1]


def test_the_arm_records_no_spans_without_a_recorder(db_conn, embedded):
    provider, exporter = _memory_provider()
    arm = VectorArm(db_conn, FakeQueryEmbedder(), ScriptedAnswerModel(), MODEL)
    with SpanRecorder(provider.get_tracer("test")).span("root", Kind.CHAIN):
        arm.run(RECORD.question)
    assert [s.name for s in exporter.get_finished_spans()] == ["root"]


@pytest.mark.parametrize(("name", "value"), [(ENDPOINT_ENV, "localhost:6006"),
                                             (TEXT_ENV, "yes")])
def test_a_bad_tracing_setting_stops_the_run_before_the_arm_opens(eval_dir, tmp_path, monkeypatch,
                                                                  capsys, name, value):
    monkeypatch.setenv(name, value)
    opened = []
    spec = ArmSpec(name="vector", required_env=(), open=lambda cache, spans=None: opened.append(1))
    code = main(["--arm", "vector"], arms={"vector": spec}, judges=JUDGES, eval_dir=eval_dir,
                runs_dir=tmp_path / "runs", today=TODAY)
    assert code == EXIT_USAGE
    assert name in capsys.readouterr().err
    assert opened == []
