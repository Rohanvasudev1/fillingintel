"""Strata and the seeded draws of the tuning and scoring sets (Step 9, ticket 04)."""
import pytest

from resolve.candidates import Rule, ScoredPair
from resolve.config import FUZZY_OFF, ResolveConfig
from resolve.sample import SET_SIZES, Stratum, draw, stratum_of

CONFIG = ResolveConfig(fuzzy_cutoff=90.0, top_k=3)


def _pair(i: int, *, fuzzy: float = 20.0, rank: int | None = 40,
          rules: frozenset[Rule] = frozenset()) -> ScoredPair:
    return ScoredPair(key_a=f"Product:a{i:04d}", key_b=f"Product:b{i:04d}", label="Product",
                      fuzzy=fuzzy, acronym=False, shared_rare_words=(), embedding_rank=rank,
                      cosine=None, rules=rules)


def test_a_pair_a_rule_fired_on_is_a_candidate():
    assert stratum_of(_pair(1, rules=frozenset({Rule.EMBEDDING}), rank=2), CONFIG) \
        is Stratum.CANDIDATE


@pytest.mark.parametrize("fuzzy, rank, expected", [
    (80.0, 40, Stratum.NEAR),  # 10 points below the cutoff
    (79.9, 40, Stratum.REST),
    (20.0, 4, Stratum.NEAR),  # just past top_k
    (20.0, 6, Stratum.NEAR),  # 2 x top_k
    (20.0, 7, Stratum.REST),
    (20.0, None, Stratum.REST),  # not embedded
])
def test_near_means_within_a_margin_of_a_rule(fuzzy, rank, expected):
    assert stratum_of(_pair(1, fuzzy=fuzzy, rank=rank), CONFIG) is expected


def test_a_rule_turned_off_has_no_near_margin():
    off = ResolveConfig(fuzzy_cutoff=FUZZY_OFF, top_k=0)
    assert stratum_of(_pair(1, fuzzy=95.0, rank=1), off) is Stratum.REST


def _population(candidates: int, near: int, rest: int) -> list[ScoredPair]:
    fired = frozenset({Rule.FUZZY})
    return ([_pair(i, fuzzy=95.0, rules=fired) for i in range(candidates)]
            + [_pair(1000 + i, rank=5) for i in range(near)]
            + [_pair(2000 + i) for i in range(rest)])


def test_a_draw_takes_the_stratum_sizes_and_records_the_populations():
    drawn = draw(_population(200, 80, 500), "scoring", CONFIG)
    counts = {s: sum(p.stratum is s for p in drawn.pairs) for s in Stratum}
    assert counts == dict(SET_SIZES)
    assert drawn.populations == {Stratum.CANDIDATE: 200, Stratum.NEAR: 80, Stratum.REST: 500}
    assert drawn.config_hash == CONFIG.config_hash


def test_a_short_stratum_is_taken_whole():
    drawn = draw(_population(10, 3, 500), "tuning", CONFIG)
    assert sum(p.stratum is Stratum.CANDIDATE for p in drawn.pairs) == 10
    assert sum(p.stratum is Stratum.NEAR for p in drawn.pairs) == 3


def test_the_draw_is_the_same_every_time_and_differs_by_set():
    pairs = _population(200, 80, 500)
    assert draw(pairs, "tuning", CONFIG) == draw(list(reversed(pairs)), "tuning", CONFIG)
    tuning = {p.keys for p in draw(pairs, "tuning", CONFIG).pairs}
    scoring = {p.keys for p in draw(pairs, "scoring", CONFIG).pairs}
    assert tuning != scoring


def test_excluded_pairs_are_never_drawn_or_counted():
    pairs = _population(200, 80, 500)
    tuning = draw(pairs, "tuning", CONFIG)
    scoring = draw(pairs, "scoring", CONFIG, exclude={p.keys for p in tuning.pairs})
    assert not {p.keys for p in scoring.pairs} & {p.keys for p in tuning.pairs}
    assert scoring.populations[Stratum.CANDIDATE] == 200 - SET_SIZES[Stratum.CANDIDATE]


def test_an_unknown_set_is_rejected():
    with pytest.raises(ValueError, match="set"):
        draw(_population(1, 1, 1), "final", CONFIG)
