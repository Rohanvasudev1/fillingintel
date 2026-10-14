"""``python -m eval.local_hops``: retrieval-only dev scores at 1 and 2 hops, and the
choice between them (Step 11 ticket 05).  Offline: the arm is a fake that
retrieves from the records' own gold chunks."""
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from eval.local_hops import EXIT_COULD_NOT_RUN, EXIT_OK, main
from eval.schema import dump_records, load_records
from ingest.provenance import REPO_ROOT
from retrieve.arm import ArmError, RetrievedChunk

RECORDS = load_records(REPO_ROOT / "eval" / "agent_drafted_set.jsonl")
DEV = [r for r in RECORDS if r.split == "dev"]
GOLD = {r.question: list(r.gold_chunk_ids) for r in RECORDS}


class FakeArm:
    """Finds every gold chunk at the hop counts in *finds*, nothing otherwise."""

    def __init__(self, hops: int, finds: frozenset[int]):
        self._found = hops in finds

    def retrieve(self, question: str):
        ids = GOLD[question] if self._found else []
        return SimpleNamespace(retrieved=tuple(RetrievedChunk(c, 1.0) for c in ids),
                               graph=SimpleNamespace(no_anchor=not ids))


@pytest.fixture
def eval_dir(tmp_path):
    directory = tmp_path / "eval"
    dump_records(DEV[:30], directory / "agent_drafted_set.jsonl")
    (directory / "eval_set.jsonl").write_text("", encoding="utf-8")
    return directory


def _main(eval_dir, tmp_path, finds: set[int], **kwargs) -> int:
    return main([], open_arm=lambda hops: nullcontext(FakeArm(hops, frozenset(finds))),
                eval_dir=eval_dir, report_dir=tmp_path / "reports", commit="abc1234", **kwargs)


def _report(tmp_path) -> dict:
    (path,) = (tmp_path / "reports").glob("local-hops-run*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_higher_pooled_recall_at_10_is_chosen(eval_dir, tmp_path):
    assert _main(eval_dir, tmp_path, {2}) == EXIT_OK
    report = _report(tmp_path)
    assert report["chosen_hops"] == 2
    assert report["scores"]["2"]["pooled"]["recall@10"] == 1.0
    assert report["scores"]["1"]["pooled"]["recall@10"] == 0.0


def test_a_tie_goes_to_one_hop(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, {1, 2})
    assert _report(tmp_path)["chosen_hops"] == 1


def test_the_report_records_provenance_and_counts(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, {1})
    report = _report(tmp_path)
    answerable = [r for r in DEV[:30] if r.class_ not in ("decline", "unanswerable")]
    assert report["commit"] == "abc1234"
    assert report["questions"] == "agent-drafted questions, dev, answerable only"
    assert report["n"] == len(answerable)
    assert report["no_anchor"]["2"]["pooled"] == len(answerable)
    assert report["no_anchor"]["1"]["pooled"] == 0
    assert set(report["config"]) >= {"anchors", "name_match_cutoff", "k", "embedding_model"}


def test_runs_are_numbered(eval_dir, tmp_path):
    _main(eval_dir, tmp_path, {1})
    _main(eval_dir, tmp_path, {1})
    names = sorted(p.name for p in (tmp_path / "reports").glob("*.json"))
    assert names == ["local-hops-run1.json", "local-hops-run2.json"]


def test_an_arm_error_stops_with_nothing_written(eval_dir, tmp_path, capsys):
    def broken(hops):
        raise ArmError("graph down")

    code = main([], open_arm=broken, eval_dir=eval_dir, report_dir=tmp_path / "reports",
                commit="abc1234")
    assert code == EXIT_COULD_NOT_RUN
    assert "graph down" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()
