"""`python -m resolve.score`: inputs, checks, the report header and exit codes (Step 9)."""
import json

import pytest

from resolve.labels import PairLabel, append_labels, draw_path, labels_path, write_draw
from resolve.sample import Draw, DrawnPair, Stratum
from resolve.score import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE, main

GROQ, GROQ_INC, HUAWEI = "Organization:groq", "Organization:groq, inc.", "Organization:huawei"
PAIRS = (DrawnPair(GROQ, GROQ_INC, "Organization", Stratum.CANDIDATE),
         DrawnPair(GROQ, HUAWEI, "Organization", Stratum.REST))
DRAW = Draw(set_name="scoring", seed=1, config_hash="c" * 64,
            populations={Stratum.CANDIDATE: 40, Stratum.NEAR: 0, Stratum.REST: 900},
            pairs=PAIRS)


def _label(pair: DrawnPair, same: bool) -> PairLabel:
    return PairLabel(set_name="scoring", key_a=pair.key_a, key_b=pair.key_b, label=pair.label,
                     stratum=pair.stratum, same=same, quote_a="q", chunk_id_a="c",
                     quote_b="q", chunk_id_b="c", quotes_verified=True, reason="r",
                     model="gpt-6-luna", effort="high", prompt_version="label/v1@00000000")


@pytest.fixture
def out(tmp_path):
    write_draw(draw_path(tmp_path, "scoring"), DRAW, {})
    append_labels(labels_path(tmp_path, "scoring"), [_label(PAIRS[0], True),
                                                     _label(PAIRS[1], False)])
    (tmp_path / "merge_log.jsonl").write_text("", encoding="utf-8")
    report = {"provenance": {"config_hash": "c" * 64},
              "adjudication": {"model": "claude-sonnet-5-5", "effort": "high",
                               "prompt_version": "resolve/v1@00000000",
                               "decisions": [{"key_a": GROQ, "key_b": GROQ_INC,
                                              "answer": "same"}]}}
    (tmp_path / "run1.json").write_text(json.dumps(report), encoding="utf-8")
    return tmp_path


def test_a_score_report_with_its_header(out):
    assert main([], out_dir=out) == EXIT_OK
    report = json.loads((out / "scoring-score1.json").read_text(encoding="utf-8"))
    header = report["provenance"]
    assert header["note"] == "agent-labelled (gpt-6-luna)"
    assert (header["labeller"]["model"], header["labeller"]["effort"]) == ("gpt-6-luna", "high")
    assert header["adjudicator"] == {"model": "claude-sonnet-5-5", "effort": "high",
                                     "prompt_version": "resolve/v1@00000000"}
    assert header["config_hash"] == {"draw": "c" * 64, "run": "c" * 64}
    assert {"commit", "ontology_version", "files", "bootstrap"} <= set(header)
    assert len(header["labeller"]["prompt"]["sha256"]) == 64
    # nothing merged: Groq / Groq, Inc. is a missed match, so recall is 0, precision undefined
    assert report["recall"]["value"] == 0.0
    assert report["precision"]["value"] is None
    assert report["adjudicator"]["correct"] == 1
    assert report["unrepresented_strata"] == []
    assert main([], out_dir=out) == EXIT_OK  # a second report never replaces the first
    assert (out / "scoring-score2.json").exists()


def test_an_edited_alias_map_fails_the_check(out):
    (out / "alias_map.json").write_text(json.dumps({GROQ_INC: GROQ}), encoding="utf-8")
    assert main([], out_dir=out) == EXIT_CHECK_FAILED


def test_a_label_for_an_undrawn_pair_fails_the_check(out):
    stray = DrawnPair(GROQ_INC, HUAWEI, "Organization", Stratum.REST)
    append_labels(labels_path(out, "scoring"), [_label(stray, False)])
    assert main([], out_dir=out) == EXIT_CHECK_FAILED


def test_missing_inputs_cannot_run(out):
    assert main(["--set", "tuning"], out_dir=out) == EXIT_RUN_ERROR
    (out / "run1.json").unlink()
    assert main([], out_dir=out) == EXIT_RUN_ERROR


def test_malformed_inputs_are_bad_input(out):
    (out / "run1.json").write_text("[", encoding="utf-8")
    assert main([], out_dir=out) == EXIT_USAGE


def test_bad_arguments(out):
    assert main(["--set", "final"], out_dir=out) == EXIT_USAGE


def test_a_scoring_run_under_another_config_fails_the_check(out):
    report = json.loads((out / "run1.json").read_text(encoding="utf-8"))
    report["provenance"]["config_hash"] = "d" * 64
    (out / "run2.json").write_text(json.dumps(report), encoding="utf-8")
    assert main([], out_dir=out) == EXIT_CHECK_FAILED


def test_a_stratum_without_labels_fails_the_scoring_check(tmp_path):
    drawn = Draw(set_name="scoring", seed=1, config_hash="c" * 64,
                 populations={Stratum.CANDIDATE: 40, Stratum.NEAR: 0, Stratum.REST: 900},
                 pairs=PAIRS)
    write_draw(draw_path(tmp_path, "scoring"), drawn, {})
    append_labels(labels_path(tmp_path, "scoring"), [_label(PAIRS[0], True)])  # rest failed
    (tmp_path / "run1.json").write_text(json.dumps({"provenance": {"config_hash": "c" * 64}}),
                                        encoding="utf-8")
    assert main([], out_dir=tmp_path) == EXIT_CHECK_FAILED
