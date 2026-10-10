"""Cheap scores on same-label pairs, and the rules that make a pair a candidate (Step 9).

Named labels: every same-label pair is scored, so pairs outside the candidate
set can still be sampled for recall. A pair is a candidate when any rule fires:
- normalized_equal: equal match keys;
- fuzzy: RapidFuzz token_set_ratio of the match keys at or above the cutoff;
- acronym: a one-word name equals the initials of the other's words, with or
  without the small words ("SEC", "DOJ");
- rare_word: the match keys share a word found in few match keys of the label;
- embedding: either node is among the other's top-k neighbours by cosine,
  ties broken by key.

Segments and MarketPlatforms belong to one filer (their keys hold its CIK,
ADR-0005), so they pair only with nodes of the same filer. MetricValues pair
only within one filing on equal subject, period, value and unit (same_facts),
and RiskFactors are never paired (Step 10's PERSISTS_AS).
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations

from rapidfuzz import fuzz

from resolve.config import ResolveConfig
from resolve.nodes import METRIC_LABEL, RISK_LABEL, ResolveNode
from resolve.normalize import match_key

PER_FILER_LABELS = frozenset({"Segment", "MarketPlatform"})
_SMALL_WORDS = frozenset({"of", "and", "the", "for", "on", "in", "to", "a", "an"})
Vectors = Mapping[str, Sequence[float]]


class Rule(StrEnum):
    NORMALIZED_EQUAL = "normalized_equal"
    FUZZY = "fuzzy"
    ACRONYM = "acronym"
    RARE_WORD = "rare_word"
    EMBEDDING = "embedding"
    SAME_FACTS = "same_facts"


@dataclass(frozen=True, slots=True)
class ScoredPair:
    """Two same-label nodes (key_a < key_b), their cheap scores and the rules that fired."""

    key_a: str
    key_b: str
    label: str
    fuzzy: float
    acronym: bool
    shared_rare_words: tuple[str, ...]
    embedding_rank: int | None  # the better of the two ranks; None when not embedded
    cosine: float | None
    rules: frozenset[Rule]

    @property
    def is_candidate(self) -> bool:
        return bool(self.rules)


def is_named(label: str) -> bool:
    """Whether a label's nodes are compared by name and embedded."""
    return label not in (METRIC_LABEL, RISK_LABEL)


def score_pairs(nodes: Sequence[ResolveNode], vectors: Vectors,
                config: ResolveConfig) -> tuple[ScoredPair, ...]:
    """Every pair the rules can see, sorted by label (and filer, for per-filer labels)."""
    by_block: dict[tuple[str, tuple[str, ...]], list[ResolveNode]] = {}
    for n in nodes:
        filer = n.filer_ciks if n.label in PER_FILER_LABELS else ()
        by_block.setdefault((n.label, filer), []).append(n)
    pairs: list[ScoredPair] = []
    for (label, _), group in sorted(by_block.items()):
        group.sort(key=lambda n: n.key)
        if label == METRIC_LABEL:
            pairs.extend(_metric_pairs(group))
        elif is_named(label):
            pairs.extend(_named_pairs(label, group, vectors, config))
    return tuple(pairs)


def _metric_pairs(group: Sequence[ResolveNode]) -> Iterable[ScoredPair]:
    by_facts: dict[object, list[ResolveNode]] = {}
    for n in group:
        by_facts.setdefault(n.metric, []).append(n)
    for same in by_facts.values():
        for a, b in combinations(same, 2):
            ka, kb = match_key(a.name), match_key(b.name)
            rules = {Rule.SAME_FACTS} | ({Rule.NORMALIZED_EQUAL} if ka == kb else set())
            yield ScoredPair(key_a=a.key, key_b=b.key, label=METRIC_LABEL,
                             fuzzy=fuzz.token_set_ratio(ka, kb), acronym=False,
                             shared_rare_words=(), embedding_rank=None, cosine=None,
                             rules=frozenset(rules))


def _named_pairs(label: str, group: Sequence[ResolveNode], vectors: Vectors,
                 config: ResolveConfig) -> Iterable[ScoredPair]:
    keys = {n.key: match_key(n.name) for n in group}
    rare = _rare_words(keys.values(), config)
    ranks = _embedding_ranks(group, vectors) if config.top_k else {}
    for a, b in combinations(group, 2):
        ka, kb = keys[a.key], keys[b.key]
        fuzzy = fuzz.token_set_ratio(ka, kb)
        acronym = _is_acronym(ka, kb) or _is_acronym(kb, ka)
        shared = tuple(sorted(set(ka.split()) & set(kb.split()) & rare))
        rank, cosine = ranks.get((a.key, b.key), (None, None))
        rules = {rule for rule, fired in (
            (Rule.NORMALIZED_EQUAL, bool(ka) and ka == kb),
            (Rule.FUZZY, fuzzy >= config.fuzzy_cutoff),
            (Rule.ACRONYM, acronym),
            (Rule.RARE_WORD, bool(shared)),
            (Rule.EMBEDDING, rank is not None and rank <= config.top_k),
        ) if fired}
        yield ScoredPair(key_a=a.key, key_b=b.key, label=label, fuzzy=fuzzy, acronym=acronym,
                         shared_rare_words=shared, embedding_rank=rank, cosine=cosine,
                         rules=frozenset(rules))


def _rare_words(keys: Iterable[str], config: ResolveConfig) -> frozenset[str]:
    """Words in at most rare_word_max_keys distinct match keys, long and not small words."""
    counts = Counter(word for key in set(keys) for word in set(key.split()))
    return frozenset(w for w, n in counts.items()
                     if n <= config.rare_word_max_keys and len(w) >= config.rare_word_min_length
                     and w not in _SMALL_WORDS)


def _is_acronym(short: str, long: str) -> bool:
    """Whether one-word *short* is the initials of *long*'s words, with or without small words."""
    words = long.split()
    if " " in short or len(short) < 2 or len(words) < 2:
        return False
    content = [w for w in words if w not in _SMALL_WORDS]
    return short in {"".join(w[0] for w in words), "".join(w[0] for w in content)}


def _embedding_ranks(group: Sequence[ResolveNode], vectors: Vectors
                     ) -> dict[tuple[str, str], tuple[int, float]]:
    """(key_a, key_b) to the better of each node's rank of the other, and their cosine."""
    missing = [n.key for n in group if n.key not in vectors]
    if missing:
        raise ValueError(f"no vector for {len(missing)} nodes, such as {missing[0]}")
    unit = {n.key: _unit(vectors[n.key]) for n in group}
    cosine = {(a.key, b.key): sum(x * y for x, y in zip(unit[a.key], unit[b.key], strict=True))
              for a, b in combinations(group, 2)}
    best: dict[tuple[str, str], int] = {}
    for n in group:
        others = sorted((o for o in group if o.key != n.key),
                        key=lambda o: (-cosine[_ordered(n.key, o.key)], o.key))
        for rank, o in enumerate(others, start=1):
            pair = _ordered(n.key, o.key)
            best[pair] = min(best.get(pair, rank), rank)
    return {pair: (rank, cosine[pair]) for pair, rank in best.items()}


def _ordered(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in vector))
    if not norm:
        raise ValueError("a zero vector has no direction")
    return tuple(x / norm for x in vector)
