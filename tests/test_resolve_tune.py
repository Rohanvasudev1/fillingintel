"""Tuning numbers on the labelled tuning set: rules found and adjudicator agreement (ticket 06)."""
import json

from resolve.candidates import Rule, ScoredPair
from resolve.config import ResolveConfig
from resolve.labeller import load_label_prompt
from resolve.labels import read_frozen
from resolve.prompt import load_prompt
from resolve.tune import EXIT_CHECK_FAILED, EXIT_OK, main
from resolve.tuning import TunedLabel, adjudicator_check, rule_check


def _pair(a, b, *rules):
    return ScoredPair(key_a=a, key_b=b, label="Product", fuzzy=0.0, acronym=False,
                      shared_rare_words=(), embedding_rank=None, cosine=None,
                      rules=frozenset(rules))


LABELS = (TunedLabel("A", "B", same=True), TunedLabel("C", "D", same=True),
          TunedLabel("E", "F", same=False), TunedLabel("G", "H", same=False))


def test_rule_check_counts_matches_found_and_non_matches_flagged():
    pairs = (_pair("A", "B", Rule.FUZZY), _pair("C", "D"), _pair("E", "F", Rule.EMBEDDING),
             _pair("G", "H"), _pair("X", "Y", Rule.FUZZY))
    check = rule_check(pairs, pending=7, labels=LABELS)
    assert (check.pending, check.matches_found, check.matches) == (7, 1, 2)
    assert (check.non_matches_flagged, check.non_matches) == (1, 2)
    assert check.missed == (("C", "D"),)


def test_rule_check_finds_a_labelled_pair_in_either_key_order():
    check = rule_check((_pair("B", "A", Rule.FUZZY),), pending=1, labels=LABELS[:1])
    assert check.matches_found == 1


def test_a_labelled_pair_no_rule_scored_counts_as_missed():
    check = rule_check((), pending=0, labels=LABELS[:1])
    assert check.missed == (("A", "B"),)


def test_adjudicator_check_counts_agreement_on_the_pairs_it_answered():
    merged = {("A", "B"): True, ("C", "D"): False, ("E", "F"): True}  # G-H not adjudicated
    check = adjudicator_check(merged, LABELS)
    assert (check.answered, check.agreed) == (3, 1)
    assert check.merged_but_not_same == (("E", "F"),)
    assert check.same_but_not_merged == (("C", "D"),)


# ── python -m resolve.tune --freeze ───────────────────────────────────────────

def _tuning_report(directory, config_hash, prompt_version):
    report = {"rules": [{"name": "chosen", "config_hash": config_hash}],
              "adjudicator": {"prompt_version": prompt_version}}
    (directory / "tuning-report.json").write_text(json.dumps(report), encoding="utf-8")


def test_freeze_needs_a_tuning_report(tmp_path):
    assert main(["--freeze"], out_dir=tmp_path) == EXIT_CHECK_FAILED
    assert not (tmp_path / "frozen.json").exists()


def test_freeze_refuses_a_report_made_under_other_settings(tmp_path):
    _tuning_report(tmp_path, "0" * 64, load_prompt().prompt_version)
    assert main(["--freeze"], out_dir=tmp_path) == EXIT_CHECK_FAILED
    assert not (tmp_path / "frozen.json").exists()


def test_freeze_writes_the_chosen_settings_once(tmp_path):
    _tuning_report(tmp_path, ResolveConfig().config_hash, load_prompt().prompt_version)
    assert main(["--freeze"], out_dir=tmp_path) == EXIT_OK
    frozen = read_frozen(tmp_path / "frozen.json")
    assert frozen.config_hash == ResolveConfig().config_hash
    assert frozen.prompts == {"label": load_label_prompt().prompt_version,
                              "resolve": load_prompt().prompt_version}
    assert main(["--freeze"], out_dir=tmp_path) == EXIT_CHECK_FAILED
