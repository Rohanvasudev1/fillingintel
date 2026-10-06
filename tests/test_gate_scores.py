"""Quality gate scores, comparison and baseline file, with no database (Step 6 ticket 03)."""
import json

import pytest

from eval.gate_baseline import (
    BaselineError,
    GateBaseline,
    baseline_changes,
    baseline_mismatches,
    read_baseline,
    write_baseline,
)
from eval.gate_scores import (
    DECIMALS,
    POOLED,
    ClassScores,
    compare,
    question_set_sha256,
    score_questions,
)
from eval.schema import EvalRecord

GOLD_A, GOLD_B, NEG, OTHER = (f"0000000001-25-000001:{i:04d}" for i in range(4))


def _record(id_="q0001", class_="lookup", gold=(GOLD_A,), negatives=(NEG,), question=None):
    return EvalRecord.model_validate({
        "id": id_,
        "question": question or f"Question {id_}?",
        "class": class_,
        "gold_answer": "An answer.",
        "gold_chunk_ids": list(gold),
        "gold_text_sha256": {g: "0" * 64 for g in gold},
        "hard_negatives": [{"chunk_id": n, "relation": "peer_company"} for n in negatives],
        "tickers": ["NVDA"],
        "fiscal_periods": ["FY2026"],
        "topic": "t",
        "difficulty": "easy",
        "reasoning": "qualitative",
        "evidence_scope": "single",
        "provenance": "agent_drafted",
        "author": "agent",
        "split": "dev",
    })


def _scores(**overrides):
    base = {"n": 4, "recall@5": 0.5, "recall@10": 0.75, "wrong_evidence": 0.25}
    return ClassScores.model_validate({**base, **overrides})


def _baseline(**overrides):
    fields = {
        "label": "agent-drafted questions",
        "arm": "vector",
        "split": "dev",
        "commit": "abc1234",
        "snapshot_sha256": "a" * 64,
        "question_set_sha256": "b" * 64,
        "embedding_model": "voyage-4-large",
        "k": 10,
        "scores": {"lookup": _scores().model_dump(by_alias=True),
                   POOLED: _scores().model_dump(by_alias=True)},
    }
    return GateBaseline.model_validate({**fields, **overrides})


# ── scoring ──────────────────────────────────────────────────────────────────

def test_scores_are_per_class_and_pooled_over_all_questions():
    scored = [
        (_record("q0001", "lookup"), [GOLD_A]),                    # found, no wrong evidence
        (_record("q0002", "lookup"), [OTHER] * 5 + [GOLD_A]),     # gold at rank 6
        (_record("q0003", "multi_hop", gold=(GOLD_A, GOLD_B)), [NEG, GOLD_A]),
    ]
    scores = score_questions(scored)
    assert list(scores) == ["lookup", "multi_hop", POOLED]
    assert scores["lookup"].n == 2
    assert scores["lookup"].metric("recall@5") == 0.5
    assert scores["lookup"].metric("recall@10") == 1.0
    assert scores["lookup"].metric("wrong_evidence") == 0.0
    assert scores["multi_hop"].metric("recall@5") == 0.5
    assert scores["multi_hop"].metric("wrong_evidence") == 1.0  # NEG ranks above GOLD_B (missing)
    assert scores[POOLED].n == 3
    assert scores[POOLED].metric("recall@5") == pytest.approx((1 + 0 + 0.5) / 3)
    assert scores[POOLED].metric("wrong_evidence") == pytest.approx(1 / 3)


def test_a_hard_negative_ranked_below_every_gold_chunk_is_not_wrong_evidence():
    scores = score_questions([(_record(), [GOLD_A, NEG])])
    assert scores["lookup"].metric("wrong_evidence") == 0.0


def test_a_class_with_no_hard_negatives_has_no_wrong_evidence_rate():
    scores = score_questions([(_record(negatives=()), [NEG, GOLD_A])])
    assert scores["lookup"].metric("wrong_evidence") is None


@pytest.mark.parametrize("class_", ["decline", "unanswerable"])
def test_questions_without_gold_chunks_are_refused(class_):
    record = _record(class_=class_, gold=(), negatives=())
    with pytest.raises(ValueError, match=class_):
        score_questions([(record, [GOLD_A])])


def test_the_question_set_hash_changes_with_any_gated_field():
    records = [_record("q0001"), _record("q0002")]
    same = question_set_sha256([_record("q0001"), _record("q0002")])
    assert question_set_sha256(records) == same
    assert question_set_sha256(records[::-1]) != same
    assert question_set_sha256([records[0], _record("q0002", gold=(GOLD_B,))]) != same


# ── comparison ───────────────────────────────────────────────────────────────

def test_equal_scores_pass():
    assert compare({"lookup": _scores()}, {"lookup": _scores()}) == ()


def test_a_lower_recall_is_a_drop_naming_class_and_metric():
    (drop,) = compare({"lookup": _scores()}, {"lookup": _scores(**{"recall@10": 0.5})})
    assert (drop.class_, drop.metric, drop.baseline, drop.current) == (
        "lookup", "recall@10", 0.75, 0.5)
    assert drop.delta == pytest.approx(-0.25)


def test_a_higher_wrong_evidence_rate_is_a_drop():
    (drop,) = compare({"lookup": _scores()}, {"lookup": _scores(wrong_evidence=0.5)})
    assert drop.metric == "wrong_evidence"


def test_improvements_pass():
    better = _scores(**{"recall@5": 1.0, "recall@10": 1.0, "wrong_evidence": 0.0})
    assert compare({"lookup": _scores()}, {"lookup": better}) == ()


def test_differences_below_the_rounding_pass():
    tiny = 10 ** -(DECIMALS + 3)
    assert compare({"lookup": _scores()}, {"lookup": _scores(**{"recall@5": 0.5 - tiny})}) == ()


def test_different_class_sets_cannot_be_compared():
    with pytest.raises(ValueError, match="classes"):
        compare({"lookup": _scores()}, {"local": _scores()})


# ── baseline file ────────────────────────────────────────────────────────────

def test_the_baseline_round_trips_through_its_file(tmp_path):
    path = tmp_path / "gate_baseline.json"
    write_baseline(path, _baseline())
    assert read_baseline(path) == _baseline()
    assert json.loads(path.read_text())["scores"]["lookup"]["recall@5"] == 0.5
    assert path.read_text().count("\n") > 10  # indented, so an update reads as a plain diff
    assert [p.name for p in tmp_path.iterdir()] == ["gate_baseline.json"]


def test_a_missing_baseline_is_a_baseline_error(tmp_path):
    with pytest.raises(BaselineError, match="--update-baseline"):
        read_baseline(tmp_path / "nope.json")


def test_a_malformed_baseline_is_a_baseline_error(tmp_path):
    path = tmp_path / "gate_baseline.json"
    path.write_text('{"k": 10}')
    with pytest.raises(BaselineError):
        read_baseline(path)


def test_the_baseline_must_be_labelled_agent_drafted():
    with pytest.raises(ValueError):
        _baseline(label="human-written questions")


def test_matching_inputs_have_no_mismatch():
    assert baseline_mismatches(_baseline(), snapshot_sha256="a" * 64,
                               question_set_sha256="b" * 64,
                               embedding_model="voyage-4-large", k=10) == []


@pytest.mark.parametrize("field, value", [
    ("snapshot_sha256", "c" * 64),
    ("question_set_sha256", "c" * 64),
    ("embedding_model", "voyage-4"),
    ("k", 1),
])
def test_each_input_mismatch_is_named(field, value):
    current = {"snapshot_sha256": "a" * 64, "question_set_sha256": "b" * 64,
               "embedding_model": "voyage-4-large", "k": 10, field: value}
    (problem,) = baseline_mismatches(_baseline(), **current)
    assert field in problem


def test_changes_name_each_moved_number_and_flag_a_lowering():
    old = _baseline()
    new_scores = {"lookup": _scores(**{"recall@5": 0.25}).model_dump(by_alias=True),
                  POOLED: _scores(wrong_evidence=0.0).model_dump(by_alias=True)}
    lines = baseline_changes(old, _baseline(scores=new_scores, commit="def5678"))
    text = "\n".join(lines)
    assert "lookup recall@5: 0.500000 -> 0.250000" in text and "LOWERED" in text
    assert "pooled wrong_evidence: 0.250000 -> 0.000000" in text
    assert "commit: abc1234 -> def5678" in text
    assert "lookup recall@10" not in text


def test_changes_from_no_baseline_say_so():
    assert baseline_changes(None, _baseline()) == ["no previous gate baseline"]


def test_a_change_below_the_rounding_is_not_flagged_as_lowered():
    tiny = 10 ** -(DECIMALS + 3)
    noisy = {"lookup": _scores(**{"recall@5": 0.5 - tiny}).model_dump(by_alias=True),
             POOLED: _scores().model_dump(by_alias=True)}
    assert "LOWERED" not in "\n".join(baseline_changes(_baseline(), _baseline(scores=noisy)))
