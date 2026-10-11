"""score(): weighted pairwise precision and recall on the final groups (Step 9, ticket 04).

Every expected figure is worked out by hand in the test that uses it.
"""
import pytest

from eval.bootstrap import BOOTSTRAP_RESAMPLES
from resolve.sample import Stratum
from resolve.scoring import PairView, score

C, N, R = Stratum.CANDIDATE, Stratum.NEAR, Stratum.REST


def _pair(i: int, stratum: Stratum, same: bool) -> PairView:
    return PairView(key_a=f"Organization:a{i}", key_b=f"Organization:b{i}", stratum=stratum,
                    same=same)


def _merged(*pairs: PairView) -> dict[str, str]:
    """An alias map that puts each pair's two keys in one group."""
    return {p.key_b: p.key_a for p in pairs}


# candidate: 4 labelled of 300, weight 75 each; near: 2 of 50, weight 25;
# rest: 3 of 1,800, weight 600.
P1, P2, P3, P4 = _pair(1, C, True), _pair(2, C, True), _pair(3, C, False), _pair(4, C, False)
P5, P6 = _pair(5, N, True), _pair(6, N, False)
P7, P8, P9 = _pair(7, R, False), _pair(8, R, False), _pair(9, R, False)
LABELS = (P1, P2, P3, P4, P5, P6, P7, P8, P9)
STRATA = {C: 300, N: 50, R: 1800}
ALIASES = _merged(P1, P3)  # predicted same: P1 (right) and P3 (wrong)


def test_weighted_precision_and_recall_over_three_strata():
    report = score(LABELS, ALIASES, STRATA)
    # true positives: P1 = 75. Predicted same: P1 + P3 = 150. Same: P1 + P2 + P5 = 175.
    assert report.precision.value == pytest.approx(75 / 150)
    assert report.recall.value == pytest.approx(75 / 175)


def test_rules_share_counts_the_candidate_stratum_among_true_matches():
    # candidate matches P1 + P2 = 150 of the 175 weighted true matches
    assert score(LABELS, ALIASES, STRATA).rules_share.value == pytest.approx(150 / 175)


def test_raw_counts_per_stratum():
    counts = {s.stratum: s for s in score(LABELS, ALIASES, STRATA).strata}
    assert (counts[C].population, counts[C].labelled, counts[C].same, counts[C].predicted_same,
            counts[C].true_positives) == (300, 4, 2, 2, 1)
    assert counts[N].weight == pytest.approx(25.0)
    assert (counts[R].labelled, counts[R].same) == (3, 0)


def test_a_merge_through_a_chain_counts_as_predicted_same():
    a, b = _pair(1, C, True), _pair(2, C, True)
    # a.key_a and b.key_b were never compared directly; a chain put them in one group
    chained = PairView(key_a=a.key_a, key_b=b.key_b, stratum=R, same=True)
    aliases = {a.key_b: "Organization:hub", a.key_a: "Organization:hub",
               b.key_b: "Organization:hub"}
    report = score((chained,), aliases, {R: 10})
    assert report.precision.value == 1.0
    assert report.recall.value == 1.0


def test_a_key_absent_from_the_alias_map_is_its_own_canonical_key():
    pair = PairView(key_a="Organization:x", key_b="Organization:y", stratum=C, same=True)
    report = score((pair,), {"Organization:x": "Organization:y"}, {C: 1})
    assert report.precision.value == 1.0


def test_an_empty_stratum_adds_nothing_and_is_listed():
    report = score((P1, P2, P3, P4, P7), ALIASES, {C: 300, N: 50, R: 1800})
    near = next(s for s in report.strata if s.stratum is N)
    assert (near.labelled, near.weight) == (0, None)
    assert report.unrepresented == (N,)
    assert report.recall.value == pytest.approx(75 / 150)  # P1 of P1 + P2


def test_labels_in_a_stratum_with_no_population_are_rejected():
    with pytest.raises(ValueError, match="near"):
        score((P5,), {}, {C: 300})


def test_a_pair_labelled_twice_is_rejected():
    with pytest.raises(ValueError, match="twice"):
        score((P1, P1), ALIASES, STRATA)


def test_precision_is_undefined_with_nothing_predicted_same():
    report = score((P2, P5), {}, STRATA)
    assert report.precision.value is None
    assert report.precision.interval is None
    assert report.recall.value == 0.0


def test_a_known_interval_when_every_resample_agrees():
    # every labelled pair is a true positive, so every resample has precision 1
    report = score((P1, P2), _merged(P1, P2), {C: 300})
    assert report.precision.interval is not None
    assert (report.precision.interval["low"], report.precision.interval["high"]) == (1.0, 1.0)


def test_intervals_come_from_a_fixed_seed():
    first, again = score(LABELS, ALIASES, STRATA), score(LABELS, ALIASES, STRATA)
    assert first.recall.interval == again.recall.interval
    low, high = first.recall.interval["low"], first.recall.interval["high"]
    assert low <= first.recall.value <= high
    assert score(LABELS, ALIASES, STRATA, seed=7).recall.interval != first.recall.interval


def test_bootstrap_resamples_within_each_stratum():
    # Resampling across strata could draw only rest pairs and leave recall undefined;
    # within each stratum the candidate stratum always keeps its true positive.
    report = score((P1, P7), _merged(P1), {C: 300, R: 1800})
    assert report.recall.interval["resamples"] == BOOTSTRAP_RESAMPLES


def test_adjudicator_accuracy_on_the_pairs_it_judged():
    decisions = {(P1.key_a, P1.key_b): True, (P3.key_a, P3.key_b): True,
                 (P4.key_a, P4.key_b): False, (P2.key_a, P2.key_b): None}
    accuracy = score(LABELS, ALIASES, STRATA, decisions=decisions).adjudicator
    # P1 said same, is same; P3 said same, is not; P4 said not, is not. P2 had no answer.
    assert (accuracy.judged, accuracy.correct, accuracy.unusable) == (3, 2, 1)
    assert accuracy.accuracy.value == pytest.approx(2 / 3)


def test_adjudicator_accuracy_is_undefined_without_decisions():
    accuracy = score(LABELS, ALIASES, STRATA).adjudicator
    assert (accuracy.judged, accuracy.accuracy.value) == (0, None)


def test_a_hand_computed_interval_from_two_pairs():
    # One stratum, one found match and one missed: a resample's recall is 0, 1/2 or 1
    # (probabilities 1/4, 1/2, 1/4), so the 2.5th and 97.5th percentiles are 0 and 1.
    found, missed = _pair(1, C, True), _pair(2, C, True)
    report = score((found, missed), _merged(found), {C: 300})
    assert report.recall.value == 0.5
    assert (report.recall.interval["low"], report.recall.interval["high"]) == (0.0, 1.0)


def test_decisions_match_whichever_order_their_keys_come_in():
    decisions = {(P1.key_b, P1.key_a): True}
    assert score(LABELS, ALIASES, STRATA, decisions=decisions).adjudicator.correct == 1


def test_unsure_counts_as_not_same():
    # an unsure (or related, or distinct) answer arrives as False
    decisions = {(P2.key_a, P2.key_b): False}
    accuracy = score(LABELS, ALIASES, STRATA, decisions=decisions).adjudicator
    assert (accuracy.judged, accuracy.correct) == (1, 0)
