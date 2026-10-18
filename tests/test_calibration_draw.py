"""The calibration draw over the committed dev run files (Step 13 ticket 04)."""
from collections import Counter

import pytest

from eval.calibration.answers import RUNS_DIR, load_answers
from eval.calibration.draw import (
    CALIBRATION_SEED,
    DrawError,
    draw,
    read_draw,
    write_draw,
)
from eval.schema import NO_GOLD_CLASSES


@pytest.fixture(scope="module")
def pool():
    return load_answers(RUNS_DIR)


def test_answers_are_rebuilt_from_the_raw_reply(pool):
    """Each answer's kept sentences come from re-enforcing its raw reply, and match the file."""
    answer = next(a for a in pool if a.arm == "vector" and a.class_ == "lookup")
    assert answer.status == "answered"
    assert answer.kept and all(s.citations for s in answer.kept)
    assert set(answer.judged) == {"faithfulness", "answer_relevancy", "citation_support"}
    assert len(answer.judged["faithfulness"]) == 3
    assert {a.arm for a in pool} == {"vector", "graph_local", "graph_traversal", "graph_global"}


def test_draw_is_stratified_by_arm_and_class(pool):
    drawn = draw(pool, CALIBRATION_SEED, answers=24, behaviours=6)
    answers = [a for a in drawn if a.kind == "answer"]
    behaviours = [a for a in drawn if a.kind == "behaviour"]
    assert (len(answers), len(behaviours)) == (24, 6)
    strata = {(a.arm, a.class_) for a in pool if a.kind == "answer" and a.kept}
    assert {(a.arm, a.class_) for a in answers} == strata, "every stratum appears"
    assert max(Counter((a.arm, a.class_) for a in answers).values()) <= 2
    assert all(a.kept for a in answers), "faithfulness needs a kept sentence"
    assert {a.class_ for a in behaviours} == set(NO_GOLD_CLASSES)
    assert len({a.question_id for a in drawn}) == 30, "one answer per question"


def test_draw_is_fixed_by_its_seed(pool):
    first = draw(pool, CALIBRATION_SEED, answers=24, behaviours=6)
    assert [a.key for a in first] == [a.key for a in draw(pool, CALIBRATION_SEED, 24, 6)]
    assert [a.key for a in first] != [a.key for a in draw(pool, CALIBRATION_SEED + 1, 24, 6)]


def test_draw_leaves_out_excluded_questions(pool):
    first = draw(pool, CALIBRATION_SEED, answers=24, behaviours=6)
    taken = frozenset(a.question_id for a in first)
    other = draw(pool, 7, answers=30, behaviours=0, exclude_questions=taken)
    assert not taken & {a.question_id for a in other}


def test_draw_file_is_write_once(tmp_path, pool):
    path = tmp_path / "draw.json"
    drawn = draw(pool, CALIBRATION_SEED, answers=24, behaviours=6)
    assert write_draw(path, drawn, CALIBRATION_SEED, commit="abc") is True
    assert write_draw(path, drawn, CALIBRATION_SEED, commit="def") is False, "same draw: kept"
    other = draw(pool, CALIBRATION_SEED + 1, answers=24, behaviours=6)
    with pytest.raises(DrawError, match="another draw"):
        write_draw(path, other, CALIBRATION_SEED + 1, commit="abc")
    assert [e.key for e in read_draw(path).entries] == [a.key for a in drawn]
