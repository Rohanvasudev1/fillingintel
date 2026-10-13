"""The alias map check after the Step 10 replay: only MetricValue entries may move."""
from __future__ import annotations

import pytest

from resolve.check_aliases import (
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RUN_ERROR,
    EXIT_USAGE,
    AliasChange,
    alias_changes,
    main,
)
from resolve.log import Method, Stamp, append_events, merge_event, replay
from resolve.paths import ALIAS_MAP_NAME, MERGE_LOG_NAME, write_alias_map

STAMP = Stamp("cfg", "abc1234", "2026-10-13T00:00:00+00:00")
ORG = merge_event(label="Organization", members={"Organization:tsmc": 3,
                                                 "Organization:taiwan semiconductor": 1},
                  method=Method.DETERMINISTIC, rule="normalized_equal", stamp=STAMP)
OLD_MV = merge_event(label="MetricValue", members={"a:Company:1:revenue:FY2025": 2,
                                                   "a:Company:1:net revenue:FY2025": 1},
                     method=Method.LLM, decision="d-1", stamp=STAMP)
NEW_MV = merge_event(label="MetricValue", members={"a:Company:1:revenue:amount:FY2025": 2,
                                                   "a:Company:1:net revenue:amount:FY2025": 1},
                     method=Method.LLM, decision="d-2", stamp=STAMP)
PRODUCT = merge_event(label="Product", members={"Product:h100": 2, "Product:h100 gpu": 1},
                      method=Method.LLM, decision="d-3", stamp=STAMP)
LABELS = {k: e.label for e in (ORG, OLD_MV, NEW_MV, PRODUCT) for k in e.members}


def test_metric_value_entries_may_change_and_nothing_else_is_reported():
    before = replay([ORG, OLD_MV])
    after = replay([ORG, OLD_MV, NEW_MV])
    assert alias_changes(before, after, LABELS) == ()


def test_an_added_entry_of_another_label_is_a_change():
    before = replay([ORG])
    after = replay([ORG, PRODUCT])
    assert alias_changes(before, after, LABELS) == (
        AliasChange("Product", "Product:h100", None, "Product:h100"),
        AliasChange("Product", "Product:h100 gpu", None, "Product:h100"),
    )


def test_a_removed_or_moved_entry_is_a_change():
    before = {"Organization:tsmc": "Organization:tsmc",
              "Organization:taiwan semiconductor": "Organization:tsmc"}
    after = {"Organization:tsmc": "Organization:taiwan semiconductor"}
    assert alias_changes(before, after, LABELS) == (
        AliasChange("Organization", "Organization:taiwan semiconductor",
                    "Organization:tsmc", None),
        AliasChange("Organization", "Organization:tsmc", "Organization:tsmc",
                    "Organization:taiwan semiconductor"),
    )


def test_a_key_no_merge_names_is_an_error():
    with pytest.raises(ValueError, match="Organization:unknown"):
        alias_changes({"Organization:unknown": "Organization:tsmc"}, {}, LABELS)


def _files(tmp_path, before_events, after_events):
    out = tmp_path / "resolution"
    out.mkdir()
    append_events(out / MERGE_LOG_NAME, after_events)
    write_alias_map(out / ALIAS_MAP_NAME, replay(after_events))
    before = tmp_path / "before.json"
    write_alias_map(before, replay(before_events))
    return out, before


def test_the_command_exits_0_when_only_metric_values_moved(tmp_path, capsys):
    out, before = _files(tmp_path, [ORG, OLD_MV], [ORG, OLD_MV, NEW_MV])
    assert main(["--before", str(before)], out_dir=out) == EXIT_OK
    assert "0 changes" in capsys.readouterr().out


def test_the_command_exits_4_and_lists_each_change(tmp_path, capsys):
    out, before = _files(tmp_path, [ORG], [ORG, PRODUCT])
    assert main(["--before", str(before)], out_dir=out) == EXIT_CHECK_FAILED
    printed = capsys.readouterr().out
    assert "Product:h100 gpu" in printed
    assert "2 changes" in printed


def test_a_missing_before_file_exits_3(tmp_path):
    out, _ = _files(tmp_path, [ORG], [ORG])
    assert main(["--before", str(tmp_path / "absent.json")], out_dir=out) == EXIT_RUN_ERROR


def test_missing_arguments_exit_2(tmp_path):
    assert main([], out_dir=tmp_path) == EXIT_USAGE


def test_a_missing_alias_map_on_disk_exits_3(tmp_path):
    out, before = _files(tmp_path, [ORG], [ORG])
    (out / ALIAS_MAP_NAME).unlink()
    assert main(["--before", str(before)], out_dir=out) == EXIT_RUN_ERROR


def test_a_missing_merge_log_exits_3(tmp_path):
    out, before = _files(tmp_path, [ORG], [ORG])
    (out / MERGE_LOG_NAME).unlink()
    assert main(["--before", str(before)], out_dir=out) == EXIT_RUN_ERROR
