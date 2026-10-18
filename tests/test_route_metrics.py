"""Router accuracy, its Wilson interval, the confusion matrix, false declines and the
unanswerable share (Step 13 ticket 03), on constructed classifications."""
import pytest

from eval.route_metrics import Classified, route_metrics
from extract.review_round import wilson


def _rows(*pairs: tuple[str, str | None]) -> list[Classified]:
    return [Classified(f"q{i:04d}", gold, predicted) for i, (gold, predicted) in enumerate(pairs)]


def test_accuracy_counts_answerable_and_decline_and_leaves_unanswerable_out():
    rows = _rows(("lookup", "lookup"), ("lookup", "local"), ("multi_hop", "multi_hop"),
                 ("decline", "decline"), ("unanswerable", "lookup"), ("unanswerable", "decline"))
    metrics = route_metrics(rows)
    accuracy = metrics["accuracy"]
    assert (accuracy["n"], accuracy["correct"]) == (4, 3)
    assert accuracy["value"] == 0.75
    assert (accuracy["low"], accuracy["high"]) == pytest.approx(wilson(3, 4))
    assert metrics["confusion"]["lookup"] == {"lookup": 1, "local": 1}
    assert "unanswerable" not in metrics["confusion"]


def test_a_decline_question_counts_only_when_declined():
    metrics = route_metrics(_rows(("decline", "lookup"), ("decline", "decline")))
    assert metrics["accuracy"]["correct"] == 1
    assert metrics["declines"] == {"n": 2, "declined": 1, "missed": ["q0000"]}


def test_false_declines_list_every_non_decline_question_declined():
    rows = _rows(("lookup", "decline"), ("unanswerable", "decline"), ("decline", "decline"))
    assert route_metrics(rows)["false_declines"] == ["q0000", "q0001"]


def test_unanswerable_questions_get_a_share_per_predicted_class():
    rows = _rows(("unanswerable", "lookup"), ("unanswerable", "lookup"),
                 ("unanswerable", "decline"), ("unanswerable", "global"))
    assert route_metrics(rows)["unanswerable"] == {
        "n": 4, "share": {"lookup": 0.5, "decline": 0.25, "global": 0.25}}


def test_a_failed_classification_is_wrong_and_counted():
    metrics = route_metrics(_rows(("lookup", None), ("local", "local")))
    assert metrics["accuracy"]["correct"] == 1 and metrics["errors"] == ["q0000"]
    assert metrics["confusion"]["lookup"] == {"error": 1}


def test_per_class_accuracy():
    metrics = route_metrics(_rows(("global", "global"), ("global", "multi_hop"),
                                  ("local", "local")))
    assert metrics["per_class"]["global"] == {"n": 2, "correct": 1, "value": 0.5}
    assert metrics["per_class"]["local"] == {"n": 1, "correct": 1, "value": 1.0}
