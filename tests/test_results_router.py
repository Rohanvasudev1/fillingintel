"""The router section of ``benchmarks/results.md`` on constructed run records (Step 13
ticket 03): accuracy and the confusion matrix from per-question routing, false
declines, unanswerable questions, and the oracle from the mapped arms' own records."""
import pytest

from eval.results_router import (
    accuracy_lines,
    oracle_lines,
    router_accuracy,
    sweep_table,
    unanswerable_lines,
)
from eval.results_sources import ArmRuns, InputFile, ResultsInputError, RunFile


def _run(arm: str, questions: list[dict], mode: str = "full") -> RunFile:
    classes = sorted({q["class"] for q in questions})
    document = {"header": {"arm": arm, "mode": mode},
                "cells": {"agent_drafted": {arm: {c: {} for c in classes}}},
                "questions": questions}
    return RunFile(InputFile(f"benchmarks/runs/{arm}.json", "0" * 64, "abc1234"), document)


def _q(qid: str, class_: str, predicted: str | None = None, recall: float | None = None,
       status: str = "answered") -> dict:
    q = {"id": qid, "class": class_, "answer": {"status": status},
         "metrics": {} if recall is None else {"recall@10": recall}}
    if predicted is not None:
        q["routing"] = {"predicted_class": predicted}
    return q


ROUTER = _run("router", [
    _q("q1", "lookup", "lookup", 1.0), _q("q2", "lookup", "local", 0.5),
    _q("q3", "global", "multi_hop", 0.0), _q("q4", "decline", "decline", status="declined"),
    _q("q5", "decline", "lookup"), _q("q6", "unanswerable", "lookup", status="not_found"),
    _q("q7", "unanswerable", "decline", status="declined"), _q("q8", "local", "decline"),
])


def test_router_accuracy_reads_the_routing_records():
    accuracy = router_accuracy(ROUTER)
    assert (accuracy.correct, accuracy.n, accuracy.declined, accuracy.declines) == (2, 6, 1, 2)


def test_a_run_without_routing_records_is_refused():
    with pytest.raises(ResultsInputError, match="no routing record"):
        router_accuracy(_run("router", [_q("q1", "lookup")]))


def test_accuracy_lines_hold_the_confusion_matrix_and_the_declines():
    text = "\n".join(accuracy_lines(ROUTER))
    assert "2 of 6" in text and "against agent-drafted class labels" in text
    assert "| lookup | 1 | 1 | 0 | 0 | 1 of 2 |" in text  # columns lookup, local, multi_hop,
    assert "missed: `q5`" in text                         # decline
    assert "False declines (a question declined that is not a decline question): `q7`, `q8`" \
        in text


def test_unanswerable_lines_show_routing_and_answers():
    lines = unanswerable_lines({"router (pre-registered)": ArmRuns("r", (ROUTER,))})
    assert lines[-1] == ("| router (pre-registered) | 2 | lookup 0.50, decline 0.50 | 1 of 2 | "
                         "1 of 2 |")


def test_the_oracle_reads_each_gold_class_from_its_pre_registered_arm():
    arms = {
        "vector": ArmRuns("vector", (_run("vector", [_q("q1", "lookup", recall=1.0),
                                                     _q("q2", "lookup", recall=1.0),
                                                     _q("q3", "global", recall=0.9)]),)),
        "graph_global": ArmRuns("graph_global", (
            _run("graph_global", [_q("q3", "global", recall=0.2)]),
            _run("graph_global", [_q("q1", "lookup", recall=0.0)], mode="retrieval_only"))),
    }
    lines = oracle_lines(arms, {"router (pre-registered)": ArmRuns("r", (ROUTER,))})
    rows = {line.split(" | ")[0].strip("| "): line for line in lines[2:]}
    assert rows["lookup"] == "| lookup | vector | 1.000 | 0.750 |"  # oracle: vector's records
    assert rows["global"] == "| global | graph_global | 0.200 | 0.000 |"  # not vector's 0.9
    assert rows["pooled"] == "| pooled | - | 0.733 | 0.500 |"


def test_the_sweep_table_marks_the_run_the_router_uses():
    def run(version: str, effort: str, correct: int) -> dict:
        return {"prompt": {"version": version}, "effort": effort, "commit": "abc1234",
                "spent_usd": 0.006, "metrics": {
                    "accuracy": {"correct": correct, "n": 88, "value": correct / 88,
                                 "low": 0.7, "high": 0.9},
                    "declines": {"declined": 6, "n": 6}, "false_declines": []}}

    lines = sweep_table([(run("route/v1@aa", "low", 75), "r1.json"),
                         (run("route/v2@bb", "low", 76), "r4.json")], ("route/v2@bb", "low"))
    assert lines[2].endswith("|  |") and lines[3].endswith("| **yes** |")
    assert "75 of 88" in lines[2]


def test_router_rows_are_keyed_by_the_routing_map_names():
    from eval.results_inputs import DEV_ROUTER_RUNS, ROUTER_ROWS
    from retrieve.routing_maps import MAP_NAMES
    assert tuple(ROUTER_ROWS) == MAP_NAMES and set(DEV_ROUTER_RUNS) <= set(MAP_NAMES)
