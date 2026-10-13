"""Persistence links scored as Step 9 scores merges, with the selected links as the prediction
(Step 10, ticket 06). Every expected figure is worked out by hand in its test."""
import pytest

from persist.scoring import score_links
from resolve.sample import Stratum
from resolve.scoring import PairView

C, N, R = Stratum.CANDIDATE, Stratum.NEAR, Stratum.REST


def _pair(i: int, stratum: Stratum, same: bool) -> PairView:
    return PairView(key_a=f"10k:e{i}", key_b=f"10q:l{i}", stratum=stratum, same=same)


# candidate: 4 of 200, weight 50; near: 2 of 20, weight 10; rest: 2 of 1,000, weight 500.
P1, P2, P3, P4 = _pair(1, C, True), _pair(2, C, True), _pair(3, C, False), _pair(4, C, False)
P5, P6, P7, P8 = _pair(5, N, True), _pair(6, N, False), _pair(7, R, False), _pair(8, R, False)
LABELS = (P1, P2, P3, P4, P5, P6, P7, P8)
STRATA = {C: 200, N: 20, R: 1000}
LINKS = {P1.keys, P3.keys}


def test_a_labelled_pair_is_predicted_same_when_it_is_a_selected_link():
    report = score_links(LABELS, LINKS, STRATA)
    # true positives P1 = 50; predicted P1 + P3 = 100; same P1 + P2 + P5 = 110
    assert report.precision.value == pytest.approx(50 / 100)
    assert report.recall.value == pytest.approx(50 / 110)
    assert report.rules_share.value == pytest.approx(100 / 110)


def test_a_link_counts_only_in_its_own_direction():
    report = score_links((P1,), {(P1.key_b, P1.key_a)}, {C: 1})
    assert report.precision.value is None  # nothing predicted
    assert report.recall.value == 0.0


def test_adjudicator_accuracy_reads_decisions_by_pair():
    decisions = {P1.keys: True, P3.keys: False, P4.keys: None}
    accuracy = score_links(LABELS, LINKS, STRATA, decisions=decisions).adjudicator
    assert (accuracy.judged, accuracy.correct, accuracy.unusable) == (2, 2, 1)


def test_intervals_are_fixed_by_the_seed():
    first, again = score_links(LABELS, LINKS, STRATA), score_links(LABELS, LINKS, STRATA)
    assert first.recall.interval == again.recall.interval is not None
