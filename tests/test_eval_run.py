"""``python -m eval.run`` with a fake arm and fake judges (Step 5, tickets 03 to 07).

The eval directory is a temporary copy of real agent-drafted records.  The fake
arm returns a fixed ranking per question and the real question filter over the
real filing list, and the fake judge returns fixed scores per question and run,
so every cell value below is computed by hand.
"""
import hashlib
import json
import re
import subprocess
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import MappingProxyType

import pytest

from eval.judging.openai_backend import OpenAIUsage
from eval.judging.runner import JUDGES, JudgeRunError, JudgeSpec
from eval.judging.scoring import JudgeConfig, JudgeInput, RunScores, metrics_for
from eval.run import EXIT_RUN_ERROR, EXIT_USAGE, main
from eval.schema import EvalRecord, dump_records, load_records
from retrieve.answer import write_answer
from retrieve.answer_model import AnswerRequest, ApiResponse, parse_reply
from retrieve.answer_prompt import SourceChunk, load_prompt
from retrieve.arm import ArmConfig, ArmError, ArmResult, ArmSpec, RetrievedChunk
from retrieve.pricing import anthropic_cost, embedding_cost
from retrieve.question_filter import QuestionFilter, parse_question_filter
from tests.anthropic_fixtures import load, with_text
from tests.corpus_filings import corpus_filings

REPO = Path(__file__).resolve().parents[1]
_ALL = {r.id: r for r in load_records(REPO / "eval" / "agent_drafted_set.jsonl")}
DEV_IDS = ["q0072", "q0073", "q0113", "q0028", "q0004", "q0011"]  # lookup x2, multi_hop, global,
TEST_ID = "q0001"                                                  # decline, unanswerable
TEN_DEV_LOOKUPS = ["q0072", "q0073", "q0074", "q0077", "q0079", "q0080", "q0081", "q0083",
                   "q0084", "q0085"]
TODAY = date(2026, 10, 5)
RESPONSE_CACHE = Path("/nonexistent/responses")  # the fakes never touch it
KEY_ENV = "FAKE_ARM_KEY"
FILINGS = corpus_filings()
NVDA_FY2025_10K = "0001045810-25-000023"  # q0072's gold is in NVIDIA's FY2026 10-K
EMBED_MODEL, ANSWER_MODEL = "voyage-4-large", "claude-sonnet-5-5"
EMBED_TOKENS = 10
NOT_RETRIEVED = _ALL["q0073"].hard_negatives[0].chunk_id  # q0073 cites it unretrieved
RECORDED = load("answered_q0072")["response"]
GENERATION_COST = anthropic_cost(ANSWER_MODEL, parse_reply(RECORDED).usage)


def _filler(n: int) -> list[str]:
    return [f"0001045810-26-000075:{i:04d}" for i in range(n)]


def _ranking(record: EvalRecord) -> list[str]:
    """The fake arm's top 10 for each record (hand-computed scores in the tests)."""
    gold = record.gold_chunk_ids
    if record.id == "q0072":  # gold at rank 1: r@5 1, p@5 .2, r@10 1, p@10 .1
        return [gold[0], *_filler(9)]
    if record.id == "q0073":  # no gold: all 0
        return _filler(10)
    if record.id == "q0113":  # gold at ranks 2 and 7: r@5 .5, p@5 .2, r@10 1, p@10 .2
        f = _filler(7)
        return [record.hard_negatives[0].chunk_id, gold[0], *f[:4], gold[1], *f[4:]]
    if record.id == "q0028":  # 2 of 4 gold at ranks 1 and 4: r@5 .5, p@5 .4, r@10 .5, p@10 .2
        f = _filler(8)
        return [gold[0], f[0], f[1], gold[2], *f[2:]]
    return _filler(10)


def _reply(record_id: str, retrieved: list[str]) -> str:
    """The fake answer model's reply to each record, given its retrieved chunk IDs."""
    r = retrieved
    replies = {
        "q0072": f"STATUS: answered\nA rose [{r[0]}]. B fell [{r[1]}].",  # 2 of 2 kept
        "q0073": f"STATUS: answered\nA rose [{r[0]}]. B fell. C grew [{NOT_RETRIEVED}].",
        "q0113": f"STATUS: answered\nA rose [{r[0]}][{r[1]}].",
        "q0028": f"STATUS: not_found\nA rose [{r[2]}].",
        "q0004": "STATUS: declined",
    }
    return replies.get(record_id, "STATUS: not_found")


GENERATION_MS = {"q0072": 1000.0, "q0073": 3000.0}  # others 500


class ReplayModel:
    def __init__(self, text: str, api_ms: float, from_cache: bool):
        self._response = ApiResponse(with_text(RECORDED, text), api_ms, from_cache)

    def complete(self, request: AnswerRequest) -> ApiResponse:
        return self._response


class FakeArm:
    name = "fake"

    def __init__(
        self,
        records: list[EvalRecord],
        error: Exception | None = None,
        filters: dict[str, QuestionFilter] | None = None,
    ):
        self.config = ArmConfig(
            models=MappingProxyType({"embedding": EMBED_MODEL, "answer": ANSWER_MODEL}),
            k=10,
            chunker_version="1",
            efforts=MappingProxyType({"answer": "high"}),
            answer_prompt=load_prompt("v1"),
            answer_max_tokens=16_000,
        )
        self._by_question = {r.question: r for r in records}
        self._error = error
        self._filters = filters or {}  # record ID -> a filter to return instead of the parser's
        self.questions: list[str] = []

    def run(self, question: str) -> ArmResult:
        self.questions.append(question)
        if self._error is not None:
            raise self._error
        record = self._by_question[question]
        ranking = _ranking(record)
        question_filter = self._filters.get(record.id) or parse_question_filter(question, FILINGS)
        chunks = [SourceChunk(cid, "NVDA", "10-K", "FY2026", "item_7", "text") for cid in ranking]
        model = ReplayModel(_reply(record.id, ranking), GENERATION_MS.get(record.id, 500.0),
                            from_cache=record.id == "q0113")
        return ArmResult(
            retrieved=tuple(
                RetrievedChunk(cid, round(1 - i / 100, 2)) for i, cid in enumerate(ranking)
            ),
            question_filter=question_filter,
            companies_without_chunks=("INTC",) if record.id == "q0028" else (),
            embed_ms=40.0 if record.id == "q0073" else 2.5,
            search_ms=10.0,
            query_cached=record.id != "q0073",
            embed_tokens=EMBED_TOKENS,
            embed_cost_usd=embedding_cost(EMBED_MODEL, EMBED_TOKENS),
            answer=write_answer(question, chunks, model, load_prompt("v1")),
            sources=tuple(chunks),
        )


JUDGE_KEY_ENV = "FAKE_JUDGE_KEY"
JUDGE_URL_ENV = "FAKE_JUDGE_BASE_URL"  # stands in for OPENAI_BASE_URL
JUDGE_COST = 0.01  # per question and run
JUDGE_USAGE = OpenAIUsage(input_tokens=100, cached_input_tokens=10, cache_write_tokens=0,
                          output_tokens=40, reasoning_tokens=25)  # per question and run
# Each answer metric scores base + 0.1 * (run - 1); decline and not-found verdicts are fixed.
JUDGE_BASE = {"q0072": 0.6, "q0073": 0.2}
BEHAVIOUR_RUNS = {"q0004": (1.0, 1.0, 0.0), "q0011": (1.0, 1.0, 1.0)}


class FakeJudge:
    def __init__(self, records: list[EvalRecord], error: Exception | None = None):
        self.config = JudgeConfig()
        self._ids = {r.question: r.id for r in records}
        self._error = error
        self.calls: list[tuple[str, int, JudgeInput]] = []

    def judge(self, item: JudgeInput, run: int) -> RunScores:
        record_id = self._ids[item.question]
        self.calls.append((record_id, run, item))
        if self._error is not None:
            raise self._error
        if record_id in BEHAVIOUR_RUNS:
            (metric,) = metrics_for(item.class_)
            scores = {metric: BEHAVIOUR_RUNS[record_id][run - 1]}
        else:
            value = round(JUDGE_BASE.get(record_id, 0.5) + 0.1 * (run - 1), 10)
            scores = {m: value for m in metrics_for(item.class_)}
        errors = {}
        if record_id == "q0113" and run == 2:  # one unusable reply
            scores["citation_support"] = None
            errors["citation_support"] = "judge stopped with refusal"
        return RunScores(run, MappingProxyType(scores), MappingProxyType(errors),
                         JUDGE_COST, calls=3, replayed=1 if run == 1 else 0, usage=JUDGE_USAGE)


@pytest.fixture
def eval_dir(tmp_path):
    directory = tmp_path / "eval"
    dump_records([_ALL[i] for i in [*DEV_IDS, TEST_ID]], directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    return directory


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "secret-value-not-real")
    monkeypatch.setenv(JUDGE_KEY_ENV, "judge-secret-not-real")
    monkeypatch.delenv(JUDGE_URL_ENV, raising=False)


def _judge_spec(judge: FakeJudge, opened: list[Path] | None = None) -> JudgeSpec:
    def open_judge(response_cache: Path, spans=None):
        if opened is not None:
            opened.append(response_cache)
        return nullcontext(judge)

    return JudgeSpec(required_env=(JUDGE_KEY_ENV,), open=open_judge,
                     forbidden_env=(JUDGE_URL_ENV,))


def _setup(eval_dir, tmp_path, error=None, filters=None, judge_error=None):
    records = load_records(eval_dir / "agent_drafted_set.jsonl")
    arm = FakeArm(records, error, filters)
    judge = FakeJudge(records, judge_error)
    opened: list[Path] = []
    judge_opened: list[Path] = []

    def open_arm(response_cache: Path, spans=None):
        opened.append(response_cache)
        return nullcontext(arm)

    spec = ArmSpec(name="fake", required_env=(KEY_ENV,), open=open_arm)
    runs = tmp_path / "runs"

    def run(*argv: str) -> int:
        return main(["--arm", "fake", *argv], arms={"fake": spec},
                    judges=_judge_spec(judge, judge_opened), eval_dir=eval_dir, runs_dir=runs,
                    today=TODAY, response_cache=RESPONSE_CACHE,
                    uncached_root=tmp_path / "uncached")

    run.judge = judge
    run.judge_opened = judge_opened
    return arm, opened, runs, run


def _result(runs: Path) -> dict:
    (path,) = runs.glob("*.json")
    return json.loads(path.read_text())


def test_dev_is_the_default_and_the_arm_sees_only_dev_question_text(eval_dir, tmp_path, env):
    arm, _, runs, run = _setup(eval_dir, tmp_path)
    assert run() == 0
    result = _result(runs)
    assert result["header"]["split"] == "dev"
    assert [q["id"] for q in result["questions"]] == DEV_IDS
    assert arm.questions == [_ALL[i].question for i in DEV_IDS]


def test_test_split_is_refused_without_final(eval_dir, tmp_path, env, capsys):
    _, opened, runs, run = _setup(eval_dir, tmp_path)
    assert run("--split", "test") == EXIT_USAGE
    assert "--final" in capsys.readouterr().err
    assert opened == [] and not runs.exists()


def test_final_scores_the_test_split(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    assert run("--split", "test", "--final") == 0
    result = _result(runs)
    assert [q["id"] for q in result["questions"]] == [TEST_ID]
    assert result["header"]["split"] == "test"


def test_final_without_the_test_split_is_refused(eval_dir, tmp_path, env):
    _, opened, _, run = _setup(eval_dir, tmp_path)
    assert run("--final") == EXIT_USAGE
    assert opened == []


def test_a_missing_api_key_stops_the_run_before_the_arm_opens(
    eval_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv(KEY_ENV, raising=False)
    _, opened, runs, run = _setup(eval_dir, tmp_path)
    assert run() == EXIT_USAGE
    assert KEY_ENV in capsys.readouterr().err
    assert opened == [] and not runs.exists()


def test_cells_are_keyed_by_arm_and_class_with_hand_computed_means(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    cells = _result(runs)["cells"]["agent_drafted"]["fake"]
    assert list(cells) == ["lookup", "multi_hop", "global", "decline", "unanswerable"]
    # lookup: mean of q0072 (1, .2, 1, .1) and q0073 (0, 0, 0, 0)
    assert cells["lookup"]["n"] == 2
    assert cells["lookup"]["metrics"] == pytest.approx(
        {"recall@5": 0.5, "precision@5": 0.1, "recall@10": 0.5, "precision@10": 0.05}
    )
    assert cells["multi_hop"]["metrics"] == pytest.approx(
        {"recall@5": 0.5, "precision@5": 0.2, "recall@10": 1.0, "precision@10": 0.2}
    )
    assert cells["global"]["metrics"] == pytest.approx(
        {"recall@5": 0.5, "precision@5": 0.4, "recall@10": 0.5, "precision@10": 0.2}
    )
    for no_gold in ("decline", "unanswerable"):
        assert cells[no_gold]["n"] == 1
        assert cells[no_gold]["metrics"] == {}


def test_header_records_provenance_and_the_agent_drafted_label(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    header = _result(runs)["header"]
    assert header["label"] == "agent-drafted questions"
    assert re.fullmatch(r"[0-9a-f]{7,}(\+dirty)?|unknown", header["commit"])
    assert header["arm"] == "fake"
    assert header["models"] == {"embedding": EMBED_MODEL, "answer": ANSWER_MODEL,
                                "judge": "gpt-6-luna"}
    assert header["efforts"] == {"answer": "high", "judge": "medium"}
    prompt = load_prompt("v1")
    assert header["answer_prompt"] == {"version": "v1", "sha256": prompt.sha256,
                                       "path": "prompts/answer/v1.md"}
    assert header["answer_max_tokens"] == 16_000
    assert header["prices"]["date"] == "2026-10-05"
    assert set(header["prices"]["models"]) == {EMBED_MODEL, ANSWER_MODEL, "gpt-6-luna"}
    assert header["k"] == 10
    assert header["metric_ks"] == [5, 10]
    assert header["chunker_version"] == "1"
    assert isinstance(header["seed"], int)
    (question_set,) = header["question_sets"]
    expected_sha = hashlib.sha256((eval_dir / "agent_drafted_set.jsonl").read_bytes()).hexdigest()
    assert question_set == {
        "name": "agent_drafted", "label": "agent-drafted questions",
        "path": "agent_drafted_set.jsonl", "sha256": expected_sha, "records_in_split": 6,
    }
    text = json.dumps(_result(runs))
    assert "secret-value-not-real" not in text and "judge-secret-not-real" not in text


def test_each_question_record_holds_retrieved_ids_scores_and_latency(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    record = next(q for q in _result(runs)["questions"] if q["id"] == "q0113")
    expected = _ranking(_ALL["q0113"])
    assert [r["chunk_id"] for r in record["retrieved"]] == expected
    assert record["retrieved"][0]["score"] == 1.0
    assert (record["retrieval_ms"], record["embed_ms"], record["search_ms"]) == (12.5, 2.5, 10.0)
    assert record["query_cached"] is True
    assert record["class"] == "multi_hop"
    assert record["gold_chunk_ids"] == _ALL["q0113"].gold_chunk_ids
    assert record["metrics"]["recall@10"] == 1.0


def test_the_filter_report_gives_exact_match_and_filter_excluded_gold(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    report = _result(runs)["filter_report"]["agent_drafted"]
    # Every dev question's companies match its labels; q0004 ("its 2025 earnings") names a
    # bare year, so its periods do not.
    assert report["n"] == 6
    assert report["exact_match"] == pytest.approx(5 / 6)
    assert report["companies_match"] == 1.0
    assert report["periods_match"] == pytest.approx(5 / 6)
    assert report["filter_excluded_gold"] == 0
    assert report["questions_with_excluded_gold"] == []


def test_gold_outside_the_filter_is_counted(eval_dir, tmp_path, env):
    narrow = QuestionFilter(("NVDA",), ("FY2025",), ("10-K",), (NVDA_FY2025_10K,))
    _, _, runs, run = _setup(eval_dir, tmp_path, filters={"q0072": narrow})
    run()
    result = _result(runs)
    report = result["filter_report"]["agent_drafted"]
    assert report["filter_excluded_gold"] == 1
    assert report["questions_with_excluded_gold"] == ["q0072"]
    record = next(q for q in result["questions"] if q["id"] == "q0072")
    assert record["filter_excluded_gold"] == _ALL["q0072"].gold_chunk_ids


def test_each_question_record_shows_its_filter_and_companies_without_chunks(
    eval_dir, tmp_path, env
):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    records = {q["id"]: q for q in _result(runs)["questions"]}
    q0113 = records["q0113"]  # AMD's and Intel's fiscal 2025
    assert q0113["filter"]["companies"] == ["AMD", "INTC"]
    assert q0113["filter"]["periods"] == ["FY2025"]
    assert q0113["filter"]["forms"] == []
    assert len(q0113["filter"]["accession_nos"]) == 8
    assert q0113["filter_matches_labels"] is True
    assert q0113["filter_excluded_gold"] == []
    assert records["q0028"]["companies_without_chunks"] == ["INTC"]
    assert records["q0004"]["filter_matches_labels"] is False


def test_results_file_is_named_by_date_arm_split_and_commit_and_never_overwritten(
    eval_dir, tmp_path, env
):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    run()
    names = sorted(p.name for p in runs.glob("*.json"))
    commit = json.loads((runs / names[0]).read_text())["header"]["commit"]
    assert names == sorted([f"2026-10-05-fake-dev-{commit}.json",
                            f"2026-10-05-fake-dev-{commit}-2.json"])


def test_human_records_are_reported_in_their_own_column(eval_dir, tmp_path, env):
    human = _ALL["q0074"].model_copy(update={"provenance": "human_written", "author": "A Person"})
    dump_records([human], eval_dir / "eval_set.jsonl")
    _, _, runs, run = _setup(eval_dir, tmp_path)
    # The fake arm only knows the agent-drafted questions; add the human one.
    records = load_records(eval_dir / "agent_drafted_set.jsonl") + [human]
    arm = FakeArm(records)
    spec = ArmSpec(name="fake", required_env=(KEY_ENV,),
                   open=lambda _cache, _spans=None: nullcontext(arm))
    assert main(["--arm", "fake"], arms={"fake": spec}, judges=_judge_spec(FakeJudge(records)),
                eval_dir=eval_dir, runs_dir=runs, today=TODAY) == 0
    result = _result(runs)
    assert list(result["cells"]) == ["agent_drafted", "human"]
    assert result["cells"]["human"]["fake"]["lookup"]["n"] == 1
    assert result["cells"]["agent_drafted"]["fake"]["lookup"]["n"] == 2
    assert [s["name"] for s in result["header"]["question_sets"]] == ["agent_drafted", "human"]
    assert result["header"]["label"] == "agent-drafted questions"


def test_an_agent_drafted_record_in_the_human_set_is_refused(eval_dir, tmp_path, env, capsys):
    dump_records([_ALL["q0074"]], eval_dir / "eval_set.jsonl")
    _, opened, _, run = _setup(eval_dir, tmp_path)
    assert run() == EXIT_USAGE
    assert "eval_set.jsonl" in capsys.readouterr().err
    assert opened == []


def test_an_arm_error_stops_the_run_without_a_results_file(eval_dir, tmp_path, env, capsys):
    _, _, runs, run = _setup(eval_dir, tmp_path, error=ArmError("embedding failed"))
    assert run() == EXIT_RUN_ERROR
    assert "embedding failed" in capsys.readouterr().err
    assert not runs.exists() or list(runs.glob("*.json")) == []


def test_an_unwritable_results_directory_is_a_clean_run_error(eval_dir, tmp_path, env, capsys):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    runs.write_text("a file where the runs directory should be", encoding="utf-8")
    assert run() == EXIT_RUN_ERROR
    assert "could not write results" in capsys.readouterr().err


def test_git_ignores_the_runs_directory():
    path = "benchmarks/runs/2026-10-05-vector-dev-abc1234.json"
    check = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO)
    assert check.returncode == 0


def test_each_question_record_holds_the_answer_its_citations_and_dropped_sentences(
    eval_dir, tmp_path, env
):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    records = {q["id"]: q for q in _result(runs)["questions"]}
    answer = records["q0073"]["answer"]
    retrieved = [r["chunk_id"] for r in records["q0073"]["retrieved"]]
    assert answer["status"] == "answered"
    assert answer["text"] == f"A rose [{retrieved[0]}]."
    assert answer["raw_text"].startswith("STATUS: answered\n")
    assert answer["kept"] == [{"text": f"A rose [{retrieved[0]}].", "citations": [retrieved[0]]}]
    assert answer["dropped"] == [
        {"text": "B fell.", "citations": [], "reason": "no_citation"},
        {"text": f"C grew [{NOT_RETRIEVED}].", "citations": [NOT_RETRIEVED],
         "reason": "citation_not_retrieved"},
    ]
    assert (answer["sentences"], answer["citations"], answer["citations_retrieved"]) == (3, 2, 1)
    assert answer["model"] == ANSWER_MODEL and answer["stop_reason"] == "end_turn"
    assert answer["usage"]["input_tokens"] > 0
    assert records["q0004"]["answer"]["status"] == "declined"
    assert records["q0113"]["answer"]["from_cache"] is True


def test_each_question_record_holds_latency_and_cost_per_stage(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    record = next(q for q in _result(runs)["questions"] if q["id"] == "q0072")
    assert record["generation_ms"] == 1000.0
    assert record["embed_tokens"] == EMBED_TOKENS
    embed_cost = embedding_cost(EMBED_MODEL, EMBED_TOKENS)
    assert record["cost_usd"] == pytest.approx({
        "embedding": embed_cost, "generation": GENERATION_COST,
        "total": embed_cost + GENERATION_COST,
    })


def test_cells_report_structural_citation_validity_and_dropped_sentences(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    cells = _result(runs)["cells"]["agent_drafted"]["fake"]
    lookup = cells["lookup"]["answers"]  # q0072: 2 kept, 2/2 cited; q0073: 1 kept, 1/2 cited
    assert lookup["status"] == {"answered": 2}
    assert (lookup["sentences"], lookup["dropped"]) == (5, 2)
    assert lookup["dropped_by_reason"] == {"no_citation": 1, "citation_not_retrieved": 1}
    assert lookup["dropped_share"] == pytest.approx(0.4)
    assert (lookup["citations"], lookup["citations_retrieved"]) == (4, 3)
    assert lookup["structural_citation_validity"] == pytest.approx(0.75)
    assert lookup["refused"] == 0 and lookup["truncated"] == 0
    decline = cells["decline"]["answers"]
    assert decline["status"] == {"declined": 1}
    assert decline["structural_citation_validity"] is None  # nothing was cited
    assert cells["global"]["answers"]["status"] == {"not_found": 1}


def test_cells_report_p50_and_p95_latency_per_stage(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    latency = {stage: {k: v for k, v in stats.items() if k != "intervals"}
               for stage, stats in
               _result(runs)["cells"]["agent_drafted"]["fake"]["lookup"]["latency_ms"].items()}
    # generation 1000 and 3000 ms: p50 2000, p95 1000 + 0.95 * 2000 = 2900
    assert latency["generation"] == pytest.approx({"n": 2, "p50": 2000.0, "p95": 2900.0})
    assert latency["search"] == pytest.approx({"n": 2, "p50": 10.0, "p95": 10.0})
    # only q0073's query embedding was made in this run; q0072's came from the cache
    assert latency["embed"] == pytest.approx({"n": 1, "p50": 40.0, "p95": 40.0})
    assert latency["retrieval"] == pytest.approx({"n": 1, "p50": 50.0, "p95": 50.0})


def test_cells_report_mean_cost_per_query(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    cost = dict(_result(runs)["cells"]["agent_drafted"]["fake"]["lookup"]["cost_usd"])
    cost.pop("intervals")  # checked in test_latency_and_cost_carry_bootstrap_intervals
    embed_cost = embedding_cost(EMBED_MODEL, EMBED_TOKENS)
    assert cost == pytest.approx({
        "per_query_embedding": embed_cost,
        "per_query_generation": GENERATION_COST,
        "per_query_total": embed_cost + GENERATION_COST,
        "total": 2 * (embed_cost + GENERATION_COST),
    })


def test_cells_report_the_wrong_evidence_rate_overall_and_per_hard_negative_label(
    eval_dir, tmp_path, env
):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    cells = _result(runs)["cells"]["agent_drafted"]["fake"]
    # lookup: q0072's negative is neither retrieved nor cited; q0073 cites its negative
    lookup = cells["lookup"]["wrong_evidence"]
    assert (lookup["n"], lookup["wrong"], lookup["rate"]) == (2, 1, 0.5)
    assert lookup["interval"] == pytest.approx({"low": 0.0, "high": 1.0, "resamples": 10_000})
    (label,) = lookup["by_relation"]
    assert label == "same_company_other_period"
    fields = ("n", "wrong", "rate", "share_of_wrong")
    assert {k: lookup["by_relation"][label][k] for k in fields} == {
        "n": 2, "wrong": 1, "rate": 0.5, "share_of_wrong": 1.0}
    # multi_hop: q0113's first negative ranks first, above both gold chunks, and is cited
    multi_hop = cells["multi_hop"]["wrong_evidence"]
    assert (multi_hop["n"], multi_hop["wrong"], multi_hop["rate"]) == (1, 1, 1.0)
    # global: q0028's two negatives (one per label) are neither retrieved nor cited
    global_ = cells["global"]["wrong_evidence"]
    assert (global_["n"], global_["wrong"], global_["rate"]) == (1, 0, 0.0)
    assert {k: v["wrong"] for k, v in global_["by_relation"].items()} == {
        "same_company_other_period": 0, "same_company_same_filing": 0}
    # no wrong questions, so no label has a share of them
    assert global_["by_relation"]["same_company_other_period"]["share_of_wrong"] is None
    assert cells["decline"]["wrong_evidence"] == {
        "n": 0, "wrong": 0, "rate": None, "interval": None, "by_relation": {}}


def test_each_question_record_lists_its_wrong_evidence(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    records = {q["id"]: q for q in _result(runs)["questions"]}
    negative = _ALL["q0113"].hard_negatives[0].chunk_id
    assert records["q0113"]["wrong_evidence"] == [{
        "chunk_id": negative, "relation": "same_company_other_period", "rank": 1,
        "above_gold": True, "cited": True,
    }]
    assert records["q0073"]["wrong_evidence"] == [{
        "chunk_id": NOT_RETRIEVED, "relation": "same_company_other_period", "rank": None,
        "above_gold": False, "cited": True,
    }]
    assert records["q0072"]["wrong_evidence"] == []
    assert records["q0004"]["wrong_evidence"] is None  # decline: no hard negatives


def test_every_cell_metric_carries_a_bootstrap_interval(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    cells = _result(runs)["cells"]["agent_drafted"]["fake"]
    # lookup: per-question values are q0072's and q0073's, so each interval spans the two
    intervals = cells["lookup"]["intervals"]
    assert {m: (i["low"], i["high"]) for m, i in intervals.items()} == pytest.approx({
        "recall@5": (0.0, 1.0), "precision@5": (0.0, 0.2),
        "recall@10": (0.0, 1.0), "precision@10": (0.0, 0.1),
    })
    # one question: a zero-width interval at its value
    assert cells["multi_hop"]["intervals"]["recall@5"] == pytest.approx(
        {"low": 0.5, "high": 0.5, "resamples": 10_000})
    # answer ratios resample (numerator, denominator) per question: q0072 dropped 0 of 2
    # sentences and cited 2 of 2 retrieved; q0073 dropped 2 of 3 and cited 1 of 2
    answers = cells["lookup"]["answers"]["intervals"]
    assert (answers["dropped_share"]["low"], answers["dropped_share"]["high"]) == pytest.approx(
        (0.0, 4 / 6))
    validity = answers["structural_citation_validity"]
    assert (validity["low"], validity["high"]) == pytest.approx((0.5, 1.0))
    assert cells["decline"]["intervals"] == {}
    assert cells["decline"]["answers"]["intervals"]["structural_citation_validity"] is None


def test_intervals_are_reproducible_under_the_recorded_seed(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    run()
    first, second = (json.loads(p.read_text()) for p in sorted(runs.glob("*.json")))
    assert first["header"]["seed"] == second["header"]["seed"]
    assert first["header"]["bootstrap"] == {"resamples": 10_000, "confidence": 0.95}
    assert first["cells"] == second["cells"]


def test_classes_under_ten_scored_records_are_labelled_directional(tmp_path, env):
    directory = tmp_path / "eval"
    dump_records([_ALL[i] for i in [*TEN_DEV_LOOKUPS, "q0113", "q0004"]],
                 directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    _, _, runs, run = _setup(directory, tmp_path)
    run()
    cells = _result(runs)["cells"]["agent_drafted"]["fake"]
    assert {cls: (c["n"], c["directional"]) for cls, c in cells.items()} == {
        "lookup": (10, False), "multi_hop": (1, True), "decline": (1, True)}


def test_the_header_defines_wrong_evidence_and_the_intervals(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    definitions = _result(runs)["header"]["metric_definitions"]
    assert {"wrong_evidence", "interval", "directional"} <= set(definitions)


def test_latency_and_cost_carry_bootstrap_intervals(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    cell = _result(runs)["cells"]["agent_drafted"]["fake"]["lookup"]
    # generation 1000 and 3000 ms: resamples give p50 and p95 of 1000 (both 1000),
    # 3000 (both 3000) or between, so both intervals run from 1000 to 3000
    generation = cell["latency_ms"]["generation"]["intervals"]
    assert {q: (i["low"], i["high"]) for q, i in generation.items()} == pytest.approx(
        {"p50": (1000.0, 3000.0), "p95": (1000.0, 3000.0)})
    # only one question made its query embedding in this run: zero width
    assert cell["latency_ms"]["embed"]["intervals"]["p95"]["low"] == pytest.approx(40.0)
    # both questions cost the same, so every cost interval has zero width
    total = embedding_cost(EMBED_MODEL, EMBED_TOKENS) + GENERATION_COST
    interval = cell["cost_usd"]["intervals"]["per_query_total"]
    assert (interval["low"], interval["high"]) == pytest.approx((total, total))


ANSWER_METRICS = ["faithfulness", "answer_relevancy", "citation_support"]


def test_the_judge_scores_every_scored_question_three_times_from_what_the_arm_returned(
    eval_dir, tmp_path, env
):
    _, _, _, run = _setup(eval_dir, tmp_path)
    run()
    calls = run.judge.calls
    assert sorted((record_id, n) for record_id, n, _ in calls) == sorted(
        (i, n) for i in DEV_IDS for n in (1, 2, 3))
    item = next(item for record_id, _, item in calls if record_id == "q0072")
    assert item.class_ == "lookup"
    assert [s.chunk_id for s in item.sources] == _ranking(_ALL["q0072"])
    assert item.answer.status == "answered"


def test_judged_cells_show_the_mean_and_spread_labelled_uncalibrated(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    judged = _result(runs)["cells"]["agent_drafted"]["fake"]["lookup"]["judged"]
    assert judged["label"] == "uncalibrated"
    assert judged["runs"] == 3
    assert list(judged["metrics"]) == ANSWER_METRICS
    # q0072 scores .6, .7, .8 (mean .7) and q0073 .2, .3, .4 (mean .3)
    faithfulness = judged["metrics"]["faithfulness"]
    assert faithfulness["n"] == 2
    assert faithfulness["mean"] == pytest.approx(0.5)
    assert faithfulness["run_means"] == pytest.approx([0.4, 0.5, 0.6])
    assert faithfulness["spread"] == pytest.approx(0.2)
    interval = faithfulness["interval"]
    assert (interval["low"], interval["high"]) == pytest.approx((0.3, 0.7))
    assert judged["errors"] == 0
    assert judged["cost_usd"] == pytest.approx({"total": 6 * JUDGE_COST,
                                                "per_query": 3 * JUDGE_COST})
    assert (judged["calls"], judged["replayed"]) == (18, 2)


def test_decline_and_not_found_correctness_sit_in_their_own_rows(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    cells = _result(runs)["cells"]["agent_drafted"]["fake"]
    decline = cells["decline"]["judged"]["metrics"]
    assert list(decline) == ["decline_correct"]
    assert decline["decline_correct"]["mean"] == pytest.approx(2 / 3)
    assert decline["decline_correct"]["run_means"] == [1.0, 1.0, 0.0]
    assert decline["decline_correct"]["spread"] == 1.0
    unanswerable = cells["unanswerable"]["judged"]["metrics"]
    assert list(unanswerable) == ["not_found_correct"]
    assert unanswerable["not_found_correct"]["mean"] == 1.0
    for cls in ("lookup", "multi_hop", "global"):
        assert list(cells[cls]["judged"]["metrics"]) == ANSWER_METRICS


def test_an_unusable_judge_reply_is_counted_and_left_out_of_that_run(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    result = _result(runs)
    judged = result["cells"]["agent_drafted"]["fake"]["multi_hop"]["judged"]
    support = judged["metrics"]["citation_support"]
    assert support["run_means"] == [0.5, None, 0.7]
    assert support["mean"] == pytest.approx(0.6)
    assert support["spread"] == pytest.approx(0.2)
    assert judged["errors"] == 1
    record = next(q for q in result["questions"] if q["id"] == "q0113")["judged"]
    assert record["errors"] == [{"run": 2, "metric": "citation_support",
                                 "error": "judge stopped with refusal"}]


def test_each_question_record_holds_every_judge_run(eval_dir, tmp_path, env):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    records = {q["id"]: q for q in _result(runs)["questions"]}
    judged = records["q0072"]["judged"]
    assert judged["label"] == "uncalibrated"
    faithfulness = judged["metrics"]["faithfulness"]
    assert faithfulness["runs"] == pytest.approx([0.6, 0.7, 0.8])
    assert faithfulness["mean"] == pytest.approx(0.7)
    assert faithfulness["spread"] == pytest.approx(0.2)
    assert judged["cost_usd"] == pytest.approx(3 * JUDGE_COST)
    assert records["q0004"]["judged"]["metrics"]["decline_correct"]["runs"] == [1.0, 1.0, 0.0]


def test_the_header_records_the_judge_ragas_version_and_judged_definitions(
    eval_dir, tmp_path, env
):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    header = _result(runs)["header"]
    judging = header["judging"]
    assert judging["model"] == "gpt-6-luna" and judging["effort"] == "medium"
    assert judging["provider"] == "openai"
    assert judging["max_output_tokens"] == 10_000
    assert judging["openai_version"] == "3.3.0"
    assert judging["ragas_version"] == "0.4.3"
    assert judging["label"] == "uncalibrated"
    assert judging["runs"] == 3
    assert judging["relevancy_embedding"] == "voyage-4-large"
    assert set(judging["prompts"]) == {"citation_support", "behaviour"}
    assert all(len(p["sha256"]) == 64 for p in judging["prompts"].values())
    definitions = header["metric_definitions"]
    assert {"judged", *ANSWER_METRICS, "decline_correct", "not_found_correct"} <= set(definitions)
    assert "uncalibrated" in definitions["judged"]


def test_the_results_file_totals_judge_usage_over_the_run_with_reasoning_apart(
    eval_dir, tmp_path, env
):
    _, _, runs, run = _setup(eval_dir, tmp_path)
    run()
    document = _result(runs)
    judged_runs = 3 * len(document["questions"])
    assert document["judge_usage"] == {
        "calls": 3 * judged_runs,
        "replayed": len(document["questions"]),  # run 1 of each question replays one call
        "cost_usd": pytest.approx(JUDGE_COST * judged_runs),
        "input_tokens": 100 * judged_runs, "cached_input_tokens": 10 * judged_runs,
        "cache_write_tokens": 0, "output_tokens": 40 * judged_runs,
        "reasoning_tokens": 25 * judged_runs,
    }


def test_a_missing_judge_key_stops_the_run_before_the_arm_opens(
    eval_dir, tmp_path, env, monkeypatch, capsys
):
    monkeypatch.delenv(JUDGE_KEY_ENV)
    _, opened, runs, run = _setup(eval_dir, tmp_path)
    assert run() == EXIT_USAGE
    assert JUDGE_KEY_ENV in capsys.readouterr().err
    assert opened == [] and not runs.exists()


def test_a_set_judge_base_url_stops_the_run_before_the_arm_opens(
    eval_dir, tmp_path, env, monkeypatch, capsys
):
    monkeypatch.setenv(JUDGE_URL_ENV, "https://example.invalid/v1")
    _, opened, runs, run = _setup(eval_dir, tmp_path)
    assert run() == EXIT_USAGE
    err = capsys.readouterr().err
    assert JUDGE_URL_ENV in err and "example.invalid" not in err
    assert "judge-secret" not in err
    assert opened == [] and not runs.exists()


def test_the_real_judges_need_the_openai_and_voyage_keys_and_refuse_a_base_url():
    assert JUDGES.required_env == ("OPENAI_API_KEY", "VOYAGE_API_KEY")
    assert JUDGES.forbidden_env == ("OPENAI_BASE_URL",)


def test_a_judge_failure_stops_the_run_without_a_results_file(eval_dir, tmp_path, env, capsys):
    _, _, runs, run = _setup(eval_dir, tmp_path, judge_error=JudgeRunError("API unreachable"))
    assert run() == EXIT_RUN_ERROR
    assert "API unreachable" in capsys.readouterr().err
    assert not runs.exists() or list(runs.glob("*.json")) == []


def test_a_default_run_answers_and_judges_through_the_shared_response_cache(
    eval_dir, tmp_path, env
):
    _, opened, runs, run = _setup(eval_dir, tmp_path)
    assert run() == 0
    assert opened == [RESPONSE_CACHE] and run.judge_opened == [RESPONSE_CACHE]
    assert _result(runs)["header"]["response_cache"] == {"uncached": False,
                                                         "dir": RESPONSE_CACHE.name}


def test_uncached_answers_and_judges_through_one_new_empty_cache(eval_dir, tmp_path, env):
    _, opened, runs, run = _setup(eval_dir, tmp_path)
    assert run("--uncached") == 0
    (fresh,) = opened
    assert run.judge_opened == [fresh]
    assert fresh.parent == tmp_path / "uncached" and fresh != RESPONSE_CACHE
    assert fresh.is_dir() and list(fresh.iterdir()) == []  # nothing there to replay
    assert _result(runs)["header"]["response_cache"] == {"uncached": True, "dir": fresh.name}


def test_two_uncached_runs_never_share_a_cache(eval_dir, tmp_path, env):
    _, opened, _, run = _setup(eval_dir, tmp_path)
    assert run("--uncached") == 0
    assert run("--uncached") == 0
    assert len(set(opened)) == 2
