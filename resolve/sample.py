"""The strata and the seeded draws of the tuning and scoring sets (Step 9, ticket 04).

Every scored same-label pair falls in one stratum under a config:
- candidate: some candidate rule fired;
- near: no rule fired, but the pair is within a margin of one, its
  token_set_ratio at most NEAR_FUZZY_MARGIN points below the fuzzy cutoff or
  its embedding rank from top_k + 1 to NEAR_RANK_FACTOR x top_k (a rule
  turned off has no margin);
- rest: every other pair.

Both sets are drawn the same way (user decision, 2026-10-10: the tuning set is
stratified too, so it holds enough true matches to tune on): from each stratum,
SET_SIZES of its pairs at random, from a generator seeded by the set's seed,
the set name and the stratum. A stratum with fewer pairs is taken whole. The
scoring draw excludes the tuning pairs, and its populations are counted after
the exclusion, since the scored figures describe the pairs left.
"""
from __future__ import annotations

import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from resolve.candidates import ScoredPair
from resolve.config import FUZZY_OFF, ResolveConfig

SETS = ("tuning", "scoring")
SEEDS = MappingProxyType({"tuning": 20261010, "scoring": 20261011})
NEAR_FUZZY_MARGIN = 10.0
NEAR_RANK_FACTOR = 2

PairKeys = tuple[str, str]


class Stratum(StrEnum):
    CANDIDATE = "candidate"
    NEAR = "near"
    REST = "rest"


SET_SIZES = MappingProxyType({Stratum.CANDIDATE: 60, Stratum.NEAR: 25, Stratum.REST: 15})


@dataclass(frozen=True, slots=True)
class DrawnPair:
    key_a: str
    key_b: str
    label: str
    stratum: Stratum

    @property
    def keys(self) -> PairKeys:
        return (self.key_a, self.key_b)


@dataclass(frozen=True)
class Draw:
    """One set's pairs, in draw order, and the population each stratum was drawn from."""

    set_name: str
    seed: int
    config_hash: str
    populations: Mapping[Stratum, int]
    pairs: tuple[DrawnPair, ...]
    sizes: Mapping[Stratum, int] = field(default_factory=lambda: dict(SET_SIZES))
    near_fuzzy_margin: float = NEAR_FUZZY_MARGIN
    near_rank_factor: int = NEAR_RANK_FACTOR


def stratum_of(pair: ScoredPair, config: ResolveConfig) -> Stratum:
    if pair.is_candidate:
        return Stratum.CANDIDATE
    fuzzy_near = (config.fuzzy_cutoff < FUZZY_OFF
                  and pair.fuzzy >= config.fuzzy_cutoff - NEAR_FUZZY_MARGIN)
    rank_near = (config.top_k > 0 and pair.embedding_rank is not None
                 and pair.embedding_rank <= NEAR_RANK_FACTOR * config.top_k)
    return Stratum.NEAR if fuzzy_near or rank_near else Stratum.REST


def draw(pairs: Sequence[ScoredPair], set_name: str, config: ResolveConfig,
         exclude: Collection[PairKeys] = ()) -> Draw:
    """The *set_name* draw from *pairs*, leaving out the pairs in *exclude*."""
    if set_name not in SETS:
        raise ValueError(f"unknown set {set_name!r}; expected one of {SETS}")
    seed = SEEDS[set_name]
    excluded = set(exclude)
    by_stratum: dict[Stratum, list[DrawnPair]] = {s: [] for s in Stratum}
    for p in sorted(pairs, key=lambda p: (p.label, p.key_a, p.key_b)):
        if (p.key_a, p.key_b) not in excluded:
            stratum = stratum_of(p, config)
            by_stratum[stratum].append(DrawnPair(p.key_a, p.key_b, p.label, stratum))
    drawn: list[DrawnPair] = []
    for stratum, population in by_stratum.items():
        rng = random.Random(f"{seed}|{set_name}|{stratum.value}")
        drawn.extend(rng.sample(population, min(SET_SIZES[stratum], len(population))))
    return Draw(set_name=set_name, seed=seed, config_hash=config.config_hash,
                populations={s: len(p) for s, p in by_stratum.items()}, pairs=tuple(drawn))
