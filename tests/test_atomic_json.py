"""Atomic JSON writes for the on-disk caches (Step 5 ticket 05)."""
import json

import pytest

from ingest.atomic_json import write_json_atomic


def test_the_file_is_written_whole(tmp_path):
    path = tmp_path / "a" / "b.json"
    write_json_atomic(path, {"x": 1})
    assert json.loads(path.read_text()) == {"x": 1}
    assert [p.name for p in path.parent.iterdir()] == ["b.json"]


def test_a_failed_write_leaves_no_file_behind(tmp_path):
    path = tmp_path / "b.json"
    with pytest.raises(TypeError):
        write_json_atomic(path, {"x": object()})
    assert list(tmp_path.iterdir()) == []
