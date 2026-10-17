"""The dev-best routing map (Step 13 ticket 02): per answerable class the arm with the
highest dev recall@10; arms whose paired difference against it includes 0 tie, and
ties go to the lowest dev cost per question.  Inputs are the committed dev run files."""
import json

import pytest

from eval.compare import PairedDifference
from eval.dev_best import (
    DevBestError,
    choose_class,
    dev_best_choices,
    map_document,
    write_map,
)
from eval.results_inputs import DEV_RUNS
from eval.results_sources import load_split


@pytest.fixture(scope="module")
def dev():
    return load_split(DEV_RUNS)


def _diff(low: float, high: float) -> PairedDifference:
    return PairedDifference("agent_drafted", "lookup", "metrics.recall@10", 27,
                            (low + high) / 2, low, high, unpaired=0, label=None)


def test_the_highest_recall_wins_when_no_other_arm_ties():
    choice = choose_class(
        "global",
        recalls={"vector": 0.80, "graph_local": 0.43},
        differences={"graph_local": _diff(-0.58, -0.18)},
        costs={"vector": 0.031, "graph_local": 0.030},
    )
    assert choice.best == "vector"
    assert choice.chosen == "vector"
    assert [c.arm for c in choice.contenders if c.tied] == ["vector"]


def test_a_tie_goes_to_the_cheaper_arm():
    # graph_local's interval reaches 0 exactly: it ties with vector and is cheaper.
    choice = choose_class(
        "lookup",
        recalls={"vector": 1.0, "graph_local": 0.963, "graph_traversal": 0.963},
        differences={"graph_local": _diff(-0.111, 0.0), "graph_traversal": _diff(-0.111, 0.0)},
        costs={"vector": 0.0310, "graph_local": 0.0304, "graph_traversal": 0.0344},
    )
    assert choice.best == "vector"
    assert choice.chosen == "graph_local"
    assert {c.arm for c in choice.contenders if c.tied} == {
        "vector", "graph_local", "graph_traversal"}


def test_a_cost_tie_goes_to_the_earlier_arm():
    choice = choose_class(
        "local", recalls={"vector": 0.9, "graph_local": 0.9},
        differences={"graph_local": _diff(-0.1, 0.1)},
        costs={"vector": 0.03, "graph_local": 0.03})
    assert choice.chosen == "vector"


def test_on_the_dev_runs_global_goes_to_vector(dev):
    choices = {c.class_: c for c in dev_best_choices(dev)}
    assert list(choices) == ["lookup", "local", "multi_hop", "global"]
    glob = choices["global"]
    assert glob.best == "vector"
    assert glob.chosen == "vector"
    by_arm = {c.arm: c for c in glob.contenders}
    # graph_global's global cell comes from its full run: recall@10 0.458 against 0.800.
    assert by_arm["graph_global"].recall_at_10 == pytest.approx(0.4583, abs=1e-4)
    assert by_arm["graph_global"].difference.high < 0


def test_on_the_dev_runs_graph_global_lookup_reads_its_retrieval_only_run(dev):
    lookup = next(c for c in dev_best_choices(dev) if c.class_ == "lookup")
    by_arm = {c.arm: c for c in lookup.contenders}
    assert by_arm["graph_global"].recall_at_10 == pytest.approx(0.5556, abs=1e-4)
    assert by_arm["graph_global"].source.endswith("graph_global-dev-6e26741.json")


def test_the_map_file_is_written_once(dev, tmp_path):
    document = map_document(dev_best_choices(dev), dev, "abc1234")
    path = tmp_path / "router" / "dev-best-map.json"
    write_map(document, path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["map"]["global"] == "vector"
    assert (saved["label"], saved["commit"]) == ("tuned on dev", "abc1234")
    assert {c["arm"] for c in saved["classes"]["global"]["contenders"]} == {
        "vector", "graph_local", "graph_traversal", "graph_global"}
    with pytest.raises(DevBestError, match="already exists"):
        write_map(document, path)
