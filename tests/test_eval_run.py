"""``python -m eval.run`` with a fake arm (Step 5, tickets 03 and 04).

The eval directory is a temporary copy of real agent-drafted records.  The fake
arm returns a fixed ranking per question and the real question filter over the
real filing list, so every cell value below is computed by hand.
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

from eval.run import EXIT_RUN_ERROR, EXIT_USAGE, main
from eval.schema import EvalRecord, dump_records, load_records
from retrieve.arm import ArmConfig, ArmError, ArmResult, ArmSpec, RetrievedChunk
from retrieve.question_filter import QuestionFilter, parse_question_filter
from tests.corpus_filings import corpus_filings

REPO = Path(__file__).resolve().parents[1]
_ALL = {r.id: r for r in load_records(REPO / "eval" / "agent_drafted_set.jsonl")}
DEV_IDS = ["q0072", "q0073", "q0113", "q0028", "q0004", "q0011"]  # lookup x2, multi_hop, global,
TEST_ID = "q0001"                                                  # decline, unanswerable
TODAY = date(2026, 10, 5)
KEY_ENV = "FAKE_ARM_KEY"
FILINGS = corpus_filings()
NVDA_FY2025_10K = "0001045810-25-000023"  # q0072's gold is in NVIDIA's FY2026 10-K


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


class FakeArm:
    name = "fake"

    def __init__(
        self,
        records: list[EvalRecord],
        error: Exception | None = None,
        filters: dict[str, QuestionFilter] | None = None,
    ):
        self.config = ArmConfig(
            models=MappingProxyType({"embedding": "fake-embed-1"}), k=10, chunker_version="1"
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
        return ArmResult(
            retrieved=tuple(
                RetrievedChunk(cid, round(1 - i / 100, 2)) for i, cid in enumerate(ranking)
            ),
            question_filter=question_filter,
            companies_without_chunks=("INTC",) if record.id == "q0028" else (),
            embed_ms=2.5,
            search_ms=10.0,
            query_cached=True,
        )


@pytest.fixture
def eval_dir(tmp_path):
    directory = tmp_path / "eval"
    dump_records([_ALL[i] for i in [*DEV_IDS, TEST_ID]], directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    return directory


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "secret-value-not-real")


def _setup(eval_dir, tmp_path, error=None, filters=None):
    records = load_records(eval_dir / "agent_drafted_set.jsonl")
    arm = FakeArm(records, error, filters)
    opened: list[bool] = []

    def open_arm():
        opened.append(True)
        return nullcontext(arm)

    spec = ArmSpec(name="fake", required_env=(KEY_ENV,), open=open_arm)
    runs = tmp_path / "runs"

    def run(*argv: str) -> int:
        return main(["--arm", "fake", *argv], arms={"fake": spec},
                    eval_dir=eval_dir, runs_dir=runs, today=TODAY)

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
    assert header["models"] == {"embedding": "fake-embed-1"}
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
    assert "secret-value-not-real" not in json.dumps(_result(runs))


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
    arm = FakeArm(load_records(eval_dir / "agent_drafted_set.jsonl") + [human])
    spec = ArmSpec(name="fake", required_env=(KEY_ENV,), open=lambda: nullcontext(arm))
    assert main(["--arm", "fake"], arms={"fake": spec}, eval_dir=eval_dir,
                runs_dir=runs, today=TODAY) == 0
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
