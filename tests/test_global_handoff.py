"""``python -m eval.global_handoff``: graph_global's top-10 chunk handoff against the arm
as built, retrieval only on dev (Step 13 ticket 02).  Offline: the selection is a fake
whose communities' chunks carry the cosines a test chooses."""
import json
from contextlib import nullcontext

import pytest

from eval.global_handoff import EXIT_COULD_NOT_RUN, EXIT_OK, LABEL, handoff_ranking, main
from eval.schema import dump_records, load_records
from ingest.provenance import REPO_ROOT
from retrieve.arm import ArmError, RetrievedChunk
from retrieve.global_retrieval import GlobalRetrieval, ShownCommunity

RECORDS = load_records(REPO_ROOT / "eval" / "agent_drafted_set.jsonl")
DEV = [r for r in RECORDS if r.split == "dev"]
SAMPLE = [r for cls in ("lookup", "local", "multi_hop", "global")
          for r in [r for r in DEV if r.class_ == cls][:3]]
GOLD = {r.question: list(r.gold_chunk_ids) for r in RECORDS}


def _community(rank: int, chunks: list[tuple[str, float]]) -> ShownCommunity:
    return ShownCommunity(f"c{rank}", rank, 1.0 - rank / 100,
                          tuple(RetrievedChunk(c, s) for c, s in chunks))


def test_the_handoff_ranks_every_shown_chunk_by_cosine_across_communities():
    communities = (
        _community(1, [("a:0001", 0.40), ("a:0002", 0.30)]),
        _community(2, [("b:0001", 0.90), ("a:0001", 0.40)]),  # a repeat keeps one place
        _community(3, [("c:0001", 0.30)]),  # a cosine tie goes to the smaller chunk ID
    )
    assert handoff_ranking(communities, k=10) == ["b:0001", "a:0001", "a:0002", "c:0001"]
    assert handoff_ranking(communities, k=2) == ["b:0001", "a:0001"]


class FakeSelection:
    """Gold sits in the fourth community with the highest cosine; three communities
    of four filler chunks each come first, so the arm as built ranks gold past 10."""

    def retrieve(self, question: str) -> GlobalRetrieval:
        communities = [_community(r, [(f"filler:{r}{i:03d}", 0.2) for i in range(4)])
                       for r in (1, 2, 3)]
        communities.append(_community(4, [(g, 0.9) for g in GOLD[question]]))
        return GlobalRetrieval(retrieval=None, communities=tuple(communities))


@pytest.fixture
def eval_dir(tmp_path):
    directory = tmp_path / "eval"
    dump_records(SAMPLE, directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    return directory


def _run(eval_dir, tmp_path, selection) -> int:
    return main([], open_selection=lambda: nullcontext(selection), eval_dir=eval_dir,
                report_dir=tmp_path / "reports", commit="abc1234")


def test_the_report_scores_both_rankings_per_class(eval_dir, tmp_path):
    assert _run(eval_dir, tmp_path, FakeSelection()) == EXIT_OK
    (path,) = (tmp_path / "reports").glob("global-handoff-run*.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["label"] == LABEL == "ablation, dev"
    assert report["commit"] == "abc1234"
    assert report["n"] == len(SAMPLE)
    built, handoff = report["scores"]["as_built"], report["scores"]["handoff"]
    assert set(built) == {"lookup", "local", "multi_hop", "global", "pooled"}
    assert built["pooled"]["recall@10"] == 0.0
    assert handoff["pooled"]["recall@5"] > 0.0
    assert built["lookup"]["n"] == 3


def test_an_arm_error_stops_with_nothing_written(eval_dir, tmp_path, capsys):
    def broken():
        raise ArmError("graph down")

    code = main([], open_selection=broken, eval_dir=eval_dir,
                report_dir=tmp_path / "reports", commit="abc1234")
    assert code == EXIT_COULD_NOT_RUN
    assert "graph down" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()
