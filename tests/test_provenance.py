"""Tests for the git commit label written into reports and parsed files."""
import re

from ingest.provenance import git_state


def test_git_state_is_a_short_commit_with_optional_dirty_flag():
    assert re.fullmatch(r"[0-9a-f]{7,}(\+dirty)?|unknown", git_state())
