"""Exact-title matches and Voyage neighbours for risk factors in one filing pair (Step 10).

A later risk factor whose normalized title equals an earlier one's links with
no LLM call. Normalized means as the RiskFactor key normalizes it (NFKC,
casefold, collapsed whitespace), with markdown escapes dropped; punctuation
stays, so "patterns" and "patterns." differ and go to the adjudicator. Keys
are unique per filing, so an exact match is unique too.

Every other later factor gets its top-k earlier neighbours by cosine, ties
broken by key. Each factor is embedded as "title: first evidence span".
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from extract.keys import norm
from extract.spans import unescape
from resolve.nodes import ResolveNode

Vectors = Mapping[str, Sequence[float]]


@dataclass(frozen=True, slots=True)
class CandidatePair:
    """A later risk factor and one of its top-k earlier neighbours."""

    later_key: str
    earlier_key: str
    rank: int  # from 1, the nearest
    cosine: float


def title_key(title: str) -> str:
    """The form two titles are compared by."""
    return norm(unescape(title))


def embed_text(factor: ResolveNode) -> str:
    return f"{unescape(factor.name)}: {unescape(factor.evidence[0].span)}"


def exact_matches(later: Sequence[ResolveNode], earlier: Sequence[ResolveNode]
                  ) -> dict[str, ResolveNode]:
    """Later key to the earlier factor with the same normalized title."""
    by_title = {title_key(e.name): e for e in earlier}
    return {n.key: by_title[t] for n in later if (t := title_key(n.name)) in by_title}


def neighbours(later: Sequence[ResolveNode], earlier: Sequence[ResolveNode], vectors: Vectors,
               top_k: int) -> tuple[CandidatePair, ...]:
    """Each later factor's *top_k* nearest earlier factors, in later order, then rank."""
    missing = [n.key for n in (*later, *earlier) if n.key not in vectors]
    if missing:
        raise ValueError(f"no vector for {len(missing)} risk factors, such as {missing[0]}")
    unit = {n.key: _unit(vectors[n.key]) for n in (*later, *earlier)}
    pairs = []
    for n in later:
        scored = sorted(((_dot(unit[n.key], unit[e.key]), e.key) for e in earlier),
                        key=lambda s: (-s[0], s[1]))
        pairs += [CandidatePair(n.key, key, rank, cosine)
                  for rank, (cosine, key) in enumerate(scored[:top_k], start=1)]
    return tuple(pairs)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    length = math.sqrt(sum(x * x for x in vector))
    if not length:
        raise ValueError("a zero vector has no direction")
    return tuple(x / length for x in vector)
