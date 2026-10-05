"""Tests for the git commit label written into reports and parsed files."""
import re
import subprocess

from ingest.provenance import git_state


def test_git_state_is_a_short_commit_with_optional_dirty_flag():
    assert re.fullmatch(r"[0-9a-f]{7,}(\+dirty)?|unknown", git_state())


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("x\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_an_untracked_file_in_any_code_directory_marks_the_state_dirty(tmp_path):
    repo = _repo(tmp_path)
    assert not git_state(repo).endswith("+dirty")
    for directory in ("eval", "retrieve", "prompts"):
        (repo / directory).mkdir()
        (repo / directory / "new.py").write_text("x = 1\n")
        assert git_state(repo).endswith("+dirty"), directory
        (repo / directory / "new.py").unlink()


def test_untracked_scratch_output_does_not_mark_the_state_dirty(tmp_path):
    repo = _repo(tmp_path)
    (repo / "spikes").mkdir()
    (repo / "spikes" / "report.txt").write_text("x\n")
    assert not git_state(repo).endswith("+dirty")
