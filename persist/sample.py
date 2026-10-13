"""The persistence scoring population, its strata and the seeded draw (Step 10, ticket 06).

The population is every (earlier, later) risk-factor pair inside a filing pair
(persist.filings): each later factor with each factor of the earlier 10-K. A
pair's keys are earlier first, as on a PERSISTS_AS edge. Strata follow
resolve.sample:
- candidate: the titles match exactly, or the pair went to the adjudicator
  (the earlier factor is among the later one's top_k neighbours);
- near: the earlier factor ranks from top_k + 1 to NEAR_RANK_FACTOR x top_k
  among the later one's neighbours;
- rest: every other pair, including every non-exact pair of a later factor
  that matched a title exactly, since such a factor is never embedded.

The draw takes resolve.sample.SET_SIZES pairs of each stratum (60 / 25 / 15)
with its own seed, through resolve.sample.sample_strata.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from persist.config import PersistConfig
from persist.filings import Filing
from persist.pipeline import FactorEmbedder, factors_by_filing, link_factors
from resolve.nodes import RISK_LABEL, ResolveNode
from resolve.sample import SET_SIZES, DrawnPair, PairKeys, Stratum, sample_strata

SET_NAME = "scoring"
SEED = 20261014
NEAR_RANK_FACTOR = 2


@dataclass(frozen=True)
class Population:
    by_stratum: Mapping[Stratum, tuple[DrawnPair, ...]]  # each sorted by keys
    adjudicated: frozenset[PairKeys]  # the candidate pairs the adjudicator was sent


@dataclass(frozen=True)
class PersistDraw:
    """The drawn pairs, in draw order, and the population each stratum was drawn from."""

    config_hash: str
    populations: Mapping[Stratum, int]
    pairs: tuple[DrawnPair, ...]
    set_name: str = SET_NAME
    seed: int = SEED
    sizes: Mapping[Stratum, int] = field(default_factory=lambda: dict(SET_SIZES))
    near_rank_factor: int = NEAR_RANK_FACTOR


def population(filings: Sequence[Filing], factors: Sequence[ResolveNode],
               embedder: FactorEmbedder, config: PersistConfig) -> Population:
    """Every pair the links could hold, in its stratum under *config*."""
    wide = link_factors(filings, factors, embedder, None,
                        replace(config, top_k=NEAR_RANK_FACTOR * config.top_k))
    exact = {(link.earlier_key, link.later_key) for link in wide.exact}
    ranks = {(c.earlier_key, c.later_key): c.rank for c in wide.candidates}
    by_filing = factors_by_filing(factors, {f.accession_no for f in filings})
    by_stratum: dict[Stratum, list[DrawnPair]] = {s: [] for s in Stratum}
    for pair in wide.pairs:
        for lt in by_filing.get(pair.later.accession_no, ()):
            for e in by_filing.get(pair.earlier.accession_no, ()):
                keys = (e.key, lt.key)
                stratum = _stratum(keys in exact, ranks.get(keys), config.top_k)
                by_stratum[stratum].append(DrawnPair(*keys, RISK_LABEL, stratum))
    adjudicated = frozenset(k for k, rank in ranks.items() if rank <= config.top_k)
    return Population({s: tuple(sorted(ps, key=lambda p: p.keys)) for s, ps in by_stratum.items()},
                      adjudicated)


def _stratum(exact: bool, rank: int | None, top_k: int) -> Stratum:
    if exact or (rank is not None and rank <= top_k):
        return Stratum.CANDIDATE
    return Stratum.NEAR if rank is not None else Stratum.REST


def draw(pop: Population, config_hash: str) -> PersistDraw:
    return PersistDraw(config_hash=config_hash,
                       populations={s: len(ps) for s, ps in pop.by_stratum.items()},
                       pairs=sample_strata(pop.by_stratum, SEED, f"persist-{SET_NAME}"))
