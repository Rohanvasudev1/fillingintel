"""``python -m eval.global_n``: retrieval-only dev scores at each N, the choice of N on
the global questions, and the top-level ablation (Step 12 ticket 05).  Offline: the
selection is a fake whose communities hold one filler chunk each, with the record's
gold chunks in the community at the rank a test chooses."""
import json
from contextlib import nullcontext

import pytest

from eval.global_n import EXIT_COULD_NOT_RUN, EXIT_OK, choose_n, main
from eval.schema import dump_records, load_records
from graph.community import LOWER, TOP
from ingest.provenance import REPO_ROOT
from retrieve.arm import ArmError, RetrievedChunk
from retrieve.global_retrieval import N_CHOICES, GlobalRetrieval, ShownCommunity

RECORDS = load_records(REPO_ROOT / "eval" / "agent_drafted_set.jsonl")
DEV = [r for r in RECORDS if r.split == "dev"]
GOLD = {r.question: list(r.gold_chunk_ids) for r in RECORDS}
CLASS = {r.question: r.class_ for r in RECORDS}
# Some of every answerable class, all ten dev globals among them.
SAMPLE = [r for r in DEV if r.class_ == "global"] + [
    r for cls in ("lookup", "local", "multi_hop") for r in [r for r in DEV if r.class_ == cls][:5]]


class FakeSelection:
    """Global questions find their gold in the community at *global_rank*, others at
    *other_rank*; None selects no community.  12 communities, one filler chunk each."""

    def __init__(self, global_rank: int | None, other_rank: int | None = 1):
        self._ranks = {"global": global_rank}
        self._other = other_rank

    def retrieve(self, question: str) -> GlobalRetrieval:
        rank = self._ranks.get(CLASS[question], self._other)
        if rank is None:
            return GlobalRetrieval(retrieval=None, communities=())
        communities = []
        for r in range(1, max(N_CHOICES) + 1):
            ids = GOLD[question] if r == rank else [f"filler:{r:04d}"]
            communities.append(ShownCommunity(f"c{r}", r, 1.0 - r / 100,
                                              tuple(RetrievedChunk(c, 0.5) for c in ids)))
        return GlobalRetrieval(retrieval=None, communities=tuple(communities))


@pytest.fixture
def eval_dir(tmp_path):
    directory = tmp_path / "eval"
    dump_records(SAMPLE, directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    return directory


def _main(eval_dir, tmp_path, lower: FakeSelection, top: FakeSelection | None = None) -> int:
    fakes = {LOWER: lower, TOP: top or FakeSelection(None, None)}
    return main([], open_selection=lambda level: nullcontext(fakes[level]),
                eval_dir=eval_dir, report_dir=tmp_path / "reports", commit="abc1234")


def _report(tmp_path) -> dict:
    (path,) = (tmp_path / "reports").glob("global-n-run*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_n_is_chosen_by_the_global_questions_recall_at_10(eval_dir, tmp_path):
    # Gold in community 5: N = 4 misses it, N = 6 and up find it in the top 10.
    assert _main(eval_dir, tmp_path, FakeSelection(5)) == EXIT_OK
    report = _report(tmp_path)
    assert report["chosen_n"] == 6
    lower = report["scores"]["lower"]
    assert lower["4"]["global"]["recall@10"] == 0.0
    assert lower["6"]["global"]["recall@10"] > 0.0


def test_a_tie_goes_to_the_smaller_n(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, FakeSelection(1))
    assert _report(tmp_path)["chosen_n"] == 4


def test_choose_n_ignores_other_classes():
    def scores(global_recall, pooled):
        return {"global": type("S", (), {"recall_at_10": global_recall})(),
                "pooled": type("S", (), {"recall_at_10": pooled})()}

    assert choose_n({4: scores(0.5, 0.9), 6: scores(0.6, 0.1), 8: scores(0.6, 0.2)}) == 6


def test_every_n_scores_every_answerable_class_and_the_top_level(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, FakeSelection(1), top=FakeSelection(None, 1))
    report = _report(tmp_path)
    for level in ("lower", "top"):
        assert set(report["scores"][level]) == {str(n) for n in N_CHOICES}
        assert set(report["scores"][level]["4"]) == {
            "lookup", "local", "multi_hop", "global", "pooled"}
    assert report["scores"]["top"]["4"]["global"]["recall@10"] == 0.0
    assert report["scores"]["top"]["4"]["lookup"]["recall@10"] == 1.0


def test_recall_over_every_shown_chunk_is_reported_beside_recall_at_10(eval_dir, tmp_path):
    # Gold in community 12 lies past the top 10 at every N, but N = 12 shows it.
    _main(eval_dir, tmp_path, FakeSelection(12))
    shown = _report(tmp_path)["shown"]["lower"]
    assert shown["8"]["global"]["recall_shown"] == 0.0
    assert shown["12"]["global"]["recall_shown"] == 1.0
    assert shown["12"]["global"]["mean_shown_chunks"] > shown["4"]["global"]["mean_shown_chunks"]


def test_the_report_records_labels_provenance_and_config(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, FakeSelection(1))
    report = _report(tmp_path)
    assert report["commit"] == "abc1234"
    assert report["questions"] == "agent-drafted questions, dev, answerable only"
    assert report["label"] == "tuned on dev, n = 10"
    assert report["n"] == len(SAMPLE)
    assert report["config"]["n_choices"] == list(N_CHOICES)
    assert report["config"]["chunks_per_community"] == 6
    assert report["empty"]["lower"]["pooled"] == 0
    assert report["empty"]["top"]["pooled"] == len(SAMPLE)


def test_an_arm_error_stops_with_nothing_written(eval_dir, tmp_path, capsys):
    def broken(level):
        raise ArmError("graph down")

    code = main([], open_selection=broken, eval_dir=eval_dir, report_dir=tmp_path / "reports",
                commit="abc1234")
    assert code == EXIT_COULD_NOT_RUN
    assert "graph down" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()


def test_a_choice_made_by_the_tie_rule_alone_is_labelled(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, FakeSelection(1))
    report = _report(tmp_path)
    assert report["recall_at_10_ties_at_every_n"] is True
    assert "tie" in report["choice_note"]


def test_a_choice_recall_at_10_separates_is_not_labelled_a_tie(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, FakeSelection(5))
    report = _report(tmp_path)
    assert report["recall_at_10_ties_at_every_n"] is False
    assert report["choice_note"] is None
