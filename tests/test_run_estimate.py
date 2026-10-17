"""The measured-cost estimate on a committed run file (Step 13, ticket 01).

graph_global's dev run scored 10 global, 6 decline and 5 unanswerable questions;
the expected values are read off that file by hand.
"""
import pytest

from retrieve.arm import EstimateQuestion
from retrieve.run_estimate import GLOBAL_ANSWER_STAGES, GRAPH_GLOBAL_RUN, MeasuredEstimate

GLOBAL_RUN = MeasuredEstimate(GRAPH_GLOBAL_RUN, answer_stages=GLOBAL_ANSWER_STAGES)
Q0004 = EstimateQuestion("q0004", "decline")  # total 0.09037368, judges 0.0001428


def test_a_question_the_file_scored_costs_what_it_cost_there_times_the_margin():
    assert GLOBAL_RUN([Q0004], retrieval_only=False) == pytest.approx(
        1.5 * (0.09037368 + 0.0001428))


def test_retrieval_only_drops_the_answer_stages_and_the_judges():
    assert GLOBAL_RUN([Q0004], retrieval_only=True) == pytest.approx(1.5 * 1.68e-06)


def test_an_unscored_question_costs_its_class_mean():
    assert GLOBAL_RUN([EstimateQuestion("q9999", "global")], retrieval_only=False) \
        == pytest.approx(1.5 * 0.0959648015)


def test_a_question_of_a_class_the_file_lacks_costs_its_most_expensive_question():
    assert GLOBAL_RUN([EstimateQuestion("q9999", "lookup")], retrieval_only=False) \
        == pytest.approx(1.5 * 0.125297085)


def test_the_estimate_sums_the_questions():
    two = GLOBAL_RUN([Q0004, Q0004], retrieval_only=False)
    assert two == pytest.approx(2 * GLOBAL_RUN([Q0004], retrieval_only=False))


def test_an_unreadable_run_file_cannot_estimate(tmp_path):
    (tmp_path / "run.json").write_text("{}")
    with pytest.raises(ValueError, match="run.json"):
        MeasuredEstimate(tmp_path / "run.json")([Q0004], retrieval_only=False)
