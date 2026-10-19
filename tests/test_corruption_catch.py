"""Catch rates for the corruption test, from per-copy judge results (Step 13 ticket 04)."""
import pytest

from eval.calibration.catch import CopyResult, catch_rates


def _copy(kind, original, corrupted, before=None, after=((False,), (False,), (True,))):
    return CopyResult(key="vector:q1", kind=kind, sentence_index=0,
                      original_faithfulness=original, corrupted_faithfulness=corrupted,
                      original_verdicts=before, corrupted_verdicts=after)


def test_a_catch_is_a_lower_mean_faithfulness_and_a_not_supported_majority():
    copies = [
        _copy("changed_number", (1.0, 1.0, 1.0), (0.5, 1.0, 0.5),
              before=((True,), (True,), (True,))),          # caught on both
        _copy("changed_number", (0.8, 0.8, 0.8), (0.8, 0.8, 0.8),
              after=((True,), (True,), (False,))),          # caught on neither
    ]
    rates = catch_rates(copies, skipped={"changed_number": 3})["changed_number"]
    assert rates["applied"] == 2 and rates["skipped"] == 3
    faith = rates["faithfulness"]
    assert (faith["scored"], faith["caught"]) == (2, 1)
    assert faith["rate"] == pytest.approx(0.5)
    low, high = faith["wilson_95"]
    assert low < 0.5 < high
    assert faith["original_mean"] == pytest.approx(0.9)
    assert faith["corrupted_mean"] == pytest.approx((2 / 3 + 0.8) / 2)
    cite = rates["citation_support"]
    assert (cite["scored"], cite["caught"]) == (2, 1)
    assert cite["already_not_supported"] == 0


def test_unscored_copies_are_counted_not_caught():
    copies = [_copy("added_sentence", (1.0, 1.0, 1.0), (None, None, None),
                    after=(None, None, None))]
    rates = catch_rates(copies, skipped={})["added_sentence"]
    assert rates["faithfulness"]["scored"] == 0 and rates["faithfulness"]["unscored"] == 1
    assert rates["citation_support"]["scored"] == 0
    assert rates["citation_support"]["unscored"] == 1


def test_a_sentence_the_judge_already_rejected_is_counted_beside_the_catch():
    copies = [_copy("wrong_citation", (1.0,) * 3, (1.0,) * 3,
                    before=((False,), (False,), (True,)))]
    cite = catch_rates(copies, skipped={})["wrong_citation"]["citation_support"]
    assert cite["caught"] == 1 and cite["already_not_supported"] == 1


def test_every_kind_has_a_row_even_with_no_copies():
    rates = catch_rates([], skipped={"swapped_period": 2})
    assert set(rates) == {"changed_number", "swapped_period", "wrong_citation",
                          "added_sentence"}
    assert rates["swapped_period"]["faithfulness"]["rate"] is None
