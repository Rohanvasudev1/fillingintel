"""Tests for ``python -m eval.merge_drafts``: slices of agent drafts into one set (Step 4)."""
import pytest

from eval.corpus_index import ChunkIndex
from eval.merge_drafts import merge
from eval.schema import dump_records, split_for
from tests.eval_helpers import make_record


@pytest.fixture(scope="module")
def index(fixture_records):
    return ChunkIndex.from_records(fixture_records)


def _draft(index, rid, question):
    return make_record(
        index, id=rid, question=question, provenance="agent_drafted", author="claude-subagent:x"
    )


def test_merge_renumbers_in_slice_order_and_recomputes_split(tmp_path, index):
    drafts = tmp_path / "drafts"
    dump_records([_draft(index, "q0001", "Question B one?")], drafts / "b.jsonl")
    dump_records(
        [_draft(index, "q0001", "Question A one?"), _draft(index, "q0002", "Question A two?")],
        drafts / "a.jsonl",
    )
    records, dropped, problems = merge(drafts, index)
    assert [r.id for r in records] == ["q0001", "q0002", "q0003"]
    questions = [r.question for r in records]
    assert questions == ["Question A one?", "Question A two?", "Question B one?"]
    assert all(r.split == split_for(r.question) for r in records)
    assert dropped == [] and problems == []


def test_merge_drops_repeated_questions_across_slices(tmp_path, index):
    drafts = tmp_path / "drafts"
    dump_records([_draft(index, "q0001", "Same question?")], drafts / "a.jsonl")
    dump_records([_draft(index, "q0001", "  same   QUESTION? ")], drafts / "b.jsonl")
    records, dropped, problems = merge(drafts, index)
    assert len(records) == 1
    assert dropped == ["b.jsonl:q0001"]
    assert problems == []


def test_merge_reports_invalid_drafts(tmp_path, index):
    drafts = tmp_path / "drafts"
    human = make_record(index, id="q0001", question="A human one?")
    dump_records([human], drafts / "a.jsonl")
    _, _, problems = merge(drafts, index)
    assert any("drafted set" in p for p in problems)


def test_merge_of_an_empty_directory_is_an_error(tmp_path, index):
    (tmp_path / "drafts").mkdir()
    with pytest.raises(FileNotFoundError):
        merge(tmp_path / "drafts", index)


def test_slice_with_a_bad_line_names_its_file(tmp_path, index):
    drafts = tmp_path / "drafts"
    dump_records([_draft(index, "q0001", "Fine?")], drafts / "a.jsonl")
    (drafts / "b.jsonl").write_text('{"id": "q0001", "quest')
    with pytest.raises(ValueError, match="b.jsonl"):
        merge(drafts, index)


@pytest.fixture(scope="module")
def parsed_dir(tmp_path_factory, fixture_records):
    from ingest.parsed_files import write_parsed

    directory = tmp_path_factory.mktemp("parsed")
    for record in fixture_records:
        write_parsed(record, directory)
    return directory


def test_merge_cli_writes_the_set_and_reports_coverage(tmp_path, parsed_dir, index, capsys):
    from eval.merge_drafts import main
    from eval.schema import load_records

    drafts, out = tmp_path / "drafts", tmp_path / "agent_drafted_set.jsonl"
    dump_records([_draft(index, "q0001", "Merged question?")], drafts / "a.jsonl")
    args = ["--drafts-dir", str(drafts), "--output", str(out), "--parsed-dir", str(parsed_dir)]
    assert main(args) == 0
    assert [r.question for r in load_records(out)] == ["Merged question?"]
    assert "# Eval-set coverage (1 records)" in capsys.readouterr().out


def test_merge_cli_writes_nothing_when_drafts_have_problems(tmp_path, parsed_dir, index):
    from eval.merge_drafts import main

    drafts, out = tmp_path / "drafts", tmp_path / "agent_drafted_set.jsonl"
    dump_records([make_record(index, question="Human?")], drafts / "a.jsonl")
    args = ["--drafts-dir", str(drafts), "--output", str(out), "--parsed-dir", str(parsed_dir)]
    assert main(args) == 1
    assert not out.exists()


def test_merge_cli_with_no_slices_fails_cleanly(tmp_path, parsed_dir, capsys):
    from eval.merge_drafts import main

    (tmp_path / "drafts").mkdir()
    args = ["--drafts-dir", str(tmp_path / "drafts"), "--parsed-dir", str(parsed_dir)]
    assert main(args) == 2
    assert "cannot merge" in capsys.readouterr().err
