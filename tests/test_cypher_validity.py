"""``python -m eval.cypher_validity``: the retrieval-only traversal run and its Cypher
validity report (Step 11 ticket 06).  Offline: the arm is a fake whose attempts are
scripted per question."""
import json
from contextlib import contextmanager, nullcontext
from types import MappingProxyType, SimpleNamespace

import pytest

from eval.cypher_validity import EXIT_COULD_NOT_RUN, EXIT_OK, EXIT_USAGE, main, validity_rates
from eval.schema import dump_records, load_records
from ingest.provenance import REPO_ROOT
from retrieve.answer_model import TokenUsage
from retrieve.arm import (
    ArmError,
    CypherAttempt,
    GraphProvenance,
    GraphRetrieval,
    RetrievedChunk,
    StageCost,
)
from retrieve.cypher_request import CypherRequest

RECORDS = load_records(REPO_ROOT / "eval" / "agent_drafted_set.jsonl")
DEV = [r for r in RECORDS if r.split == "dev"]
SAMPLE = [next(r for r in DEV if r.class_ == cls) for cls in ("lookup", "local", "decline")]
GOLD = {r.question: list(r.gold_chunk_ids) for r in RECORDS}
OK = CypherAttempt("MATCH (a) RETURN [] AS chunk_ids", "ok", rows=1)
BAD = CypherAttempt("MATCH (s:Supplier) RETURN [] AS chunk_ids", "invalid", rule="ontology",
                    message="unknown label Supplier")
EMPTY = CypherAttempt("MATCH (a) RETURN [] AS chunk_ids", "empty", rows=1, message="no chunks")
PROVENANCE = GraphProvenance(4, "abc", "load-run4", "cypher/v1@1234abcd", "f" * 64)
GENERATION = StageCost.anthropic(900.0, "claude-sonnet-5-5", TokenUsage(100, 600),
                                 from_cache=False)


def _graph(*attempts: CypherAttempt, fallback=None, cached=False) -> GraphRetrieval:
    stages = {"cypher_generation": GENERATION if not cached else
              StageCost(GENERATION.ms, GENERATION.cost_usd, from_cache=True)}
    if len(attempts) > 1:
        stages["cypher_repair"] = GENERATION
    return GraphRetrieval(stages=MappingProxyType(stages), cypher_attempts=attempts,
                          fallback_reason=fallback)


SCRIPT = {  # by class: lookup first-pass ok; local repaired; decline falls back
    "lookup": _graph(OK),
    "local": _graph(BAD, OK),
    "decline": _graph(EMPTY, EMPTY, fallback="empty"),
}


class FakeArm:
    config = SimpleNamespace(graph=PROVENANCE, k=10)

    def __init__(self, records, script=SCRIPT):
        self._by_question = {r.question: r for r in records}
        self._script = script
        self.retrieved: list[str] = []

    def cypher_requests(self, questions):
        return tuple(CypherRequest("system", (("user", q),)) for q in questions)

    def retrieve(self, question):
        self.retrieved.append(question)
        record = self._by_question[question]
        ids = GOLD[question][:10]
        return SimpleNamespace(retrieved=tuple(RetrievedChunk(c, 1.0) for c in ids),
                               graph=self._script[record.class_])


@pytest.fixture
def eval_dir(tmp_path):
    directory = tmp_path / "eval"
    dump_records(SAMPLE, directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    return directory


@contextmanager
def _claude():
    yield SimpleNamespace(complete=lambda request: pytest.fail("no call expected"))


def _main(eval_dir, tmp_path, argv, arm=None) -> int:
    arm = arm or FakeArm(SAMPLE)
    return main(argv, open_arm=lambda model, version: nullcontext(arm), claude=_claude,
                eval_dir=eval_dir, report_dir=tmp_path / "reports",
                cache_dir=tmp_path / "cache", commit="abc1234")


def _report(tmp_path) -> dict:
    (path,) = (tmp_path / "reports").glob("cypher-validity-run*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_without_send_it_only_prints_the_estimate(eval_dir, tmp_path, capsys):
    arm = FakeArm(SAMPLE)
    assert _main(eval_dir, tmp_path, [], arm) == EXIT_OK
    assert arm.retrieved == []
    assert not (tmp_path / "reports").exists()
    assert "3 first calls not cached; estimate $" in capsys.readouterr().out


def test_an_estimate_over_the_cap_is_refused(eval_dir, tmp_path):
    arm = FakeArm(SAMPLE)
    assert _main(eval_dir, tmp_path, ["--send", "--max-cost", "0"], arm) == EXIT_USAGE
    assert arm.retrieved == []


def test_the_report_holds_the_four_rates_per_class_and_pooled(eval_dir, tmp_path):
    assert _main(eval_dir, tmp_path, ["--send"]) == EXIT_OK
    rates = _report(tmp_path)["rates"]
    assert list(rates) == ["lookup", "local", "decline", "pooled"]
    assert rates["lookup"] == {"n": 1, "first_pass_valid": 1.0, "first_pass_valid_count": 1,
                               "valid_after_repair": 1.0, "valid_after_repair_count": 1,
                               "non_empty": 1.0, "non_empty_count": 1,
                               "fallback": 0.0, "fallback_count": 0}
    assert (rates["local"]["first_pass_valid"], rates["local"]["valid_after_repair"]) == (0, 1)
    assert (rates["decline"]["non_empty"], rates["decline"]["fallback"]) == (0, 1)
    assert rates["pooled"]["first_pass_valid"] == pytest.approx(2 / 3)
    assert rates["pooled"]["fallback_count"] == 1


def test_the_report_records_provenance_cost_and_every_attempt(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, ["--send"])
    report = _report(tmp_path)
    assert report["commit"] == "abc1234"
    assert report["questions"] == "agent-drafted questions, dev, all classes"
    assert report["prompt"] == {"version": "cypher/v1@1234abcd", "sha256": "f" * 64}
    assert (report["model"], report["effort"]) == ("claude-sonnet-5-5", "high")
    assert report["graph"]["load_run"] == "load-run4"
    assert report["cost"]["calls"] == 5  # 1 + 2 + 2
    assert report["cost"]["new_spend_usd"] == pytest.approx(5 * GENERATION.cost_usd)
    local = next(q for q in report["questions_detail"] if q["class"] == "local")
    assert [a["outcome"] for a in local["attempts"]] == ["invalid", "ok"]
    assert local["attempts"][0]["rule"] == "ontology"
    decline = next(q for q in report["questions_detail"] if q["class"] == "decline")
    assert decline["fallback_reason"] == "empty"
    assert set(report["retrieval"]["scores"]) == {"lookup", "local", "pooled"}
    assert report["retrieval"]["scores"]["pooled"]["recall@10"] == 1.0


def test_a_cached_reply_counts_in_the_total_but_not_in_new_spend(eval_dir, tmp_path):
    arm = FakeArm(SAMPLE, {**SCRIPT, "lookup": _graph(OK, cached=True)})
    _main(eval_dir, tmp_path, ["--send"], arm)
    cost = _report(tmp_path)["cost"]
    assert (cost["calls"], cost["cached_calls"]) == (5, 1)
    assert cost["total_usd"] == pytest.approx(5 * GENERATION.cost_usd)
    assert cost["new_spend_usd"] == pytest.approx(4 * GENERATION.cost_usd)


def test_rates_count_a_question_with_no_attempt_as_invalid():
    rates = validity_rates([(SAMPLE[0], GraphRetrieval())])
    assert rates["pooled"]["first_pass_valid"] == 0.0


def test_an_arm_that_cannot_start_stops_the_run(eval_dir, tmp_path):
    def broken(model, version):
        raise ArmError("graph built under another ontology")
    code = main(["--send"], open_arm=broken, claude=_claude, eval_dir=eval_dir,
                report_dir=tmp_path / "reports", cache_dir=tmp_path / "cache", commit="x")
    assert code == EXIT_COULD_NOT_RUN


def test_the_prompt_version_reaches_the_arm(eval_dir, tmp_path):
    seen = []

    def opener(model, version):
        seen.append(version)
        return nullcontext(FakeArm(SAMPLE))
    main([], open_arm=opener, claude=_claude, eval_dir=eval_dir,
         report_dir=tmp_path / "reports", cache_dir=tmp_path / "cache", commit="x")
    assert seen == ["v1"]
