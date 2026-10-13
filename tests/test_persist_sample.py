"""The persistence scoring population, its strata and the seeded draw (Step 10, ticket 06)."""
from persist.config import PersistConfig
from persist.sample import SEED, SET_NAME, draw, population
from resolve.sample import SET_SIZES, DrawnPair, Stratum
from tests.persist_helpers import (
    CLIMATE,
    CURRENCY,
    FILINGS,
    GUARANTEES_10K,
    GUARANTEES_10Q,
    LITIGATION_10Q,
    SEASONAL,
    earlier,
    later,
)
from tests.test_persist_pipeline import FakeEmbedder

OLD = {t: earlier(t, o) for t, o in ((SEASONAL, 21), (GUARANTEES_10K, 53), (CURRENCY, 28),
                                     (CLIMATE, 41))}
NEW = {t: later(t, o) for t, o in ((CURRENCY, 50), (GUARANTEES_10Q, 78), (LITIGATION_10Q, 71))}
CONFIG = PersistConfig(top_k=1)  # near is then rank 2


def _keys(new: str, old: str) -> tuple[str, str]:
    return (OLD[old].key, NEW[new].key)


def _population():
    return population(FILINGS, (*OLD.values(), *NEW.values()), FakeEmbedder(), CONFIG)


def test_every_later_and_earlier_factor_pair_in_a_filing_pair_is_in_one_stratum():
    pop = _population()
    keys = [p.keys for pairs in pop.by_stratum.values() for p in pairs]
    assert len(keys) == len(set(keys)) == len(OLD) * len(NEW)
    assert {p.label for pairs in pop.by_stratum.values() for p in pairs} == {"RiskFactor"}


def test_exact_titles_and_adjudicated_neighbours_are_candidates():
    candidates = {p.keys for p in _population().by_stratum[Stratum.CANDIDATE]}
    assert candidates == {_keys(CURRENCY, CURRENCY), _keys(GUARANTEES_10Q, GUARANTEES_10K),
                          _keys(LITIGATION_10Q, CLIMATE)}


def test_the_adjudicated_pairs_are_the_neighbours_within_top_k():
    assert _population().adjudicated == frozenset({_keys(GUARANTEES_10Q, GUARANTEES_10K),
                                                   _keys(LITIGATION_10Q, CLIMATE)})


def test_near_is_a_neighbour_ranked_past_top_k_up_to_twice_it():
    near = {p.keys for p in _population().by_stratum[Stratum.NEAR]}
    assert near == {_keys(GUARANTEES_10Q, CLIMATE), _keys(LITIGATION_10Q, SEASONAL)}


def test_pairs_of_an_exactly_matched_later_factor_are_otherwise_rest():
    rest = {p.keys for p in _population().by_stratum[Stratum.REST]}
    assert {_keys(CURRENCY, t) for t in (SEASONAL, GUARANTEES_10K, CLIMATE)} <= rest
    assert len(rest) == 7


def test_a_draw_records_seed_config_and_populations():
    drawn = draw(_population(), CONFIG.config_hash)
    assert (drawn.set_name, drawn.seed, drawn.config_hash) == (SET_NAME, SEED, CONFIG.config_hash)
    assert dict(drawn.populations) == {Stratum.CANDIDATE: 3, Stratum.NEAR: 2, Stratum.REST: 7}
    assert len(drawn.pairs) == 12  # every stratum is smaller than its size, so taken whole


def test_a_draw_is_seeded_and_takes_the_stratum_sizes():
    pop = _population()
    big = type(pop)(by_stratum={s: tuple(_fake(s, n) for n in range(200)) for s in Stratum},
                    adjudicated=pop.adjudicated)
    first, again = draw(big, "h"), draw(big, "h")
    assert first.pairs == again.pairs
    assert {s: sum(p.stratum is s for p in first.pairs) for s in Stratum} == dict(SET_SIZES)


def _fake(stratum: Stratum, n: int) -> DrawnPair:
    return DrawnPair(f"a:{stratum.value}{n:03d}", f"b:{stratum.value}{n:03d}", "RiskFactor",
                     stratum)
