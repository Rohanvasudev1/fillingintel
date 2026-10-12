"""`python -m resolve.check_tuning`: the Step 9b tuning check between two runs.

It compares the alias maps two runs produced on the tuning labels, lists the
pairs whose merge status changed, and fails when a pair merged only by the
newer run is labelled not same.
"""
import json

import pytest

from resolve.check_tuning import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, main
from resolve.log import Method, Stamp, append_events, merge_event
from resolve.tuning import TunedLabel, merge_changes

STAMP = Stamp(config_hash="c" * 64, commit="abc1234", timestamp="2026-10-12T12:00:00+00:00")
EPYC, AMD_EPYC, EPYC_9005 = "Product:epyc", "Product:amd epyc", "Product:epyc 9005"
RYZEN, AMD_RYZEN = "Product:ryzen", "Product:amd ryzen"


def test_merge_changes_lists_new_and_lost_merges_and_agreement():
    before = {AMD_EPYC: EPYC, EPYC: EPYC}
    after = {AMD_RYZEN: RYZEN, RYZEN: RYZEN, EPYC_9005: EPYC, EPYC: EPYC}
    labels = (TunedLabel(EPYC, AMD_EPYC, same=True), TunedLabel(RYZEN, AMD_RYZEN, same=True),
              TunedLabel(EPYC, EPYC_9005, same=False))
    changes = merge_changes(before, after, labels)
    assert changes.newly_merged == ((RYZEN, AMD_RYZEN), (EPYC, EPYC_9005))
    assert changes.no_longer_merged == ((EPYC, AMD_EPYC),)
    assert changes.newly_merged_not_same == ((EPYC, EPYC_9005),)
    assert (changes.agreed_before, changes.agreed_after, changes.labels) == (2, 1, 3)


def _merge(*members, rule=None, decision="d-1"):
    return merge_event(label="Product", members=dict.fromkeys(members, 1), method=Method.LLM,
                       rule=rule, decision=None if rule else decision, stamp=STAMP)


def _write_run(out, n, config_hash, merges):
    report = {"provenance": {"run": n, "config_hash": config_hash},
              "merges": [{"merge_id": m.merge_id} for m in merges]}
    (out / f"run{n}.json").write_text(json.dumps(report), encoding="utf-8")


def _label_line(a, b, same):
    return {"set_name": "tuning", "key_a": min(a, b), "key_b": max(a, b), "label": "Product",
            "stratum": "candidate", "same": same, "quote_a": "q", "chunk_id_a": "c",
            "quote_b": "q", "chunk_id_b": "c", "quotes_verified": True, "reason": "test",
            "model": "gpt-6-luna", "effort": "high", "prompt_version": "label/v2@00000000"}


@pytest.fixture
def out(tmp_path):
    old = _merge(EPYC, AMD_EPYC)
    piece = _merge(RYZEN, AMD_RYZEN, rule="group_cut")
    append_events(tmp_path / "merge_log.jsonl", [old, piece])
    _write_run(tmp_path, 2, "a" * 64, [old])
    _write_run(tmp_path, 3, "b" * 64, [old, piece])
    return tmp_path


def _labels(out, *rows):
    (out / "tuning-labels.jsonl").write_text(
        "".join(json.dumps(_label_line(*row)) + "\n" for row in rows), encoding="utf-8")


def test_a_new_merge_the_labels_agree_with_passes_and_is_reported(out):
    _labels(out, (RYZEN, AMD_RYZEN, True), (EPYC, AMD_EPYC, True))
    assert main(["--before", "2", "--after", "3"], out_dir=out) == EXIT_OK
    report = json.loads((out / "tuning-check.json").read_text(encoding="utf-8"))
    assert report["newly_merged"] == [{"key_a": AMD_RYZEN, "key_b": RYZEN, "same": True}]
    assert report["newly_merged_not_same"] == []
    assert (report["agreed_before"], report["agreed_after"], report["labels"]) == (1, 2, 2)
    assert report["provenance"]["after"] == {"run": "run3.json", "config_hash": "b" * 64}
    assert report["provenance"]["note"] == "agent-labelled (gpt-6-luna)"


def test_a_new_merge_labelled_not_same_stops_the_step(out, capsys):
    _labels(out, (RYZEN, AMD_RYZEN, False))
    assert main(["--before", "2", "--after", "3"], out_dir=out) == EXIT_CHECK_FAILED
    assert "labelled not same" in capsys.readouterr().err
    report = json.loads((out / "tuning-check.json").read_text(encoding="utf-8"))
    assert report["newly_merged_not_same"] == [{"key_a": AMD_RYZEN, "key_b": RYZEN,
                                                "same": False}]


def test_a_missing_run_cannot_run(out):
    _labels(out, (RYZEN, AMD_RYZEN, True))
    assert main(["--before", "2", "--after", "4"], out_dir=out) == EXIT_RUN_ERROR


def test_a_run_naming_a_merge_the_log_lacks_cannot_run(out):
    _labels(out, (RYZEN, AMD_RYZEN, True))
    _write_run(out, 4, "b" * 64, [_merge(EPYC, EPYC_9005)])
    assert main(["--before", "2", "--after", "4"], out_dir=out) == EXIT_RUN_ERROR
