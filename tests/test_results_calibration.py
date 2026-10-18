"""``benchmarks/results.md`` labels judged columns by the calibration report (Step 13
ticket 04): a passing column says it is calibrated, a failing one leaves the table,
and before any report every judged column stays "uncalibrated"."""
import json

import pytest

from eval.results_table import build

LABEL = "calibrated, n = 24, claims split by an agent"


def _row(statistic, value, passed, **extra):
    return {"statistic": statistic, "value": value, "passed": passed, "label": LABEL,
            "interval_95": [value - 0.2, min(value + 0.1, 1.0)], **extra}


def _report(faith_passed=True, relevancy_passed=False):
    return {
        "drawn": 30, "scored": 30, "pass_at": 0.6, "commit": "abc1234",
        "reviewer": "me",
        "metrics": {
            "faithfulness": _row("spearman_rho", 0.71 if faith_passed else 0.40, faith_passed,
                                 n=24, mae=0.08, signed_bias=0.03, judge_self_agreement=0.9),
            "answer_relevancy": _row("spearman_rho", 0.55 if not relevancy_passed else 0.7,
                                     relevancy_passed, n=24, mae=0.2, signed_bias=0.1,
                                     judge_self_agreement=0.95),
            "citation_support": _row("cohen_kappa", 0.66, True, n_sentences=150,
                                     n_answers=24, percent_agreement=0.9, disagreements=15,
                                     judge_self_agreement=0.8),
            "decline_correct": {"statistic": "percent_agreement", "n": 3, "value": 1.0,
                                "disagreements": 0, "passed": None},
            "not_found_correct": {"statistic": "percent_agreement", "n": 3,
                                  "value": 2 / 3, "disagreements": 1, "passed": None},
        },
        "rescore": None,
        "pace": {"answers": 30, "median_seconds": 95.0, "total_minutes": 80.0,
                 "under_15_seconds": 0, "looked_again": 0},
    }


def _corruption():
    rate = {"scored": 25, "unscored": 0, "caught": 20, "rate": 0.8,
            "wilson_95": [0.61, 0.91]}
    return {"label": "corruption test, not calibration", "commit": "abc1234",
            "catch_rates": {k: {"applied": 25, "skipped": 5,
                                "faithfulness": {**rate, "original_mean": 0.9,
                                                 "corrupted_mean": 0.7},
                                "citation_support": {**rate, "already_not_supported": 1}}
                            for k in ("changed_number", "swapped_period", "wrong_citation",
                                      "added_sentence")}}


@pytest.fixture
def files(tmp_path):
    def write(report=None, corruption=None):
        paths = []
        for name, doc in (("report.json", report), ("corruption-run1.json", corruption)):
            path = tmp_path / name
            if doc is not None:
                path.write_text(json.dumps(doc), encoding="utf-8")
            paths.append(str(path))
        return tuple(paths)
    return write


def _header(text):
    return next(line for line in text.splitlines() if line.startswith("| class | arm | n |"))


def test_without_a_report_judged_columns_stay_uncalibrated(files):
    text = build(calibration=files())
    assert "faithfulness (uncalibrated)" in _header(text)
    assert "not scored yet" in text


def test_a_passing_column_is_labelled_and_a_failing_one_leaves_the_table(files):
    text = build(calibration=files(_report(), _corruption()))
    header = _header(text)
    assert f"faithfulness ({LABEL})" in header
    assert f"citation support ({LABEL})" in header
    assert "answer relevancy" not in header, "failed calibration"
    assert "answer relevancy failed calibration" in text
    paired = text[text.index("### `dev` paired differences"):]
    assert "| answer relevancy |" not in paired
    h4 = next(line for line in text.splitlines() if "H4, global questions" in line)
    assert h4.startswith("- **not testable**")
    lines = text.splitlines()
    start = lines.index(header)
    table = lines[start:lines.index("", start)]
    assert len({line.count("|") for line in table}) == 1, "rows lose the column too"


def test_calibration_and_catch_rates_are_in_the_measured_numbers(files):
    text = build(calibration=files(_report(), _corruption()))
    section = text[text.index("### Judge calibration"):]
    assert "spearman_rho" in section and "0.710" in section
    assert "Corruption test, not calibration" in section
    assert "| changed number |" in section


def test_behaviour_columns_say_how_far_they_agree_with_the_user(files):
    text = build(calibration=files(_report()))
    header = next(line for line in text.splitlines()
                  if line.startswith("| class | arm | n | correct"))
    assert "decline 3 of 3, not-found 2 of 3 agree with the user" in header
