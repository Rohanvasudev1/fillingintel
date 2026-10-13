"""link_factors(): one persistence pass over the corpus's risk factors (Step 10).

1. Filing pairs (persist.filings): each filing with an earlier 10-K of its
   company in the list.
2. In each pair, a later risk factor with the same normalized title as an
   earlier one links with no call (persist.candidates).
3. Every other later factor, and the earlier 10-K's factors, are embedded;
   each such later factor gets its top-k earlier neighbours as candidates.
4. Each candidate goes to the adjudicator, *workers* at a time. Without one,
   the candidates come back as pending and only exact links are made.
5. Selection (persist.links): at most one predecessor per later factor.

The function reads and writes nothing.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from persist.candidates import CandidatePair, embed_text, exact_matches, neighbours
from persist.config import PersistConfig
from persist.decisions import PersistAdjudicator, PersistDecision
from persist.filings import Filing, FilingPair, filing_pairs
from persist.links import PersistLink, exact_link, select_links
from resolve.nodes import RISK_LABEL, ResolveNode
from resolve.pipeline import in_order


class FactorEmbedder(Protocol):
    """One vector per text, in order."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


@dataclass(frozen=True, slots=True)
class PairCounts:
    """What one filing pair holds and what linked."""

    later_accession: str
    earlier_accession: str
    later_factors: int
    earlier_factors: int
    exact: int
    candidates: int
    linked_exact: int
    linked_adjudicated: int


@dataclass(frozen=True)
class Linking:
    """What one pass found and decided."""

    pairs: tuple[FilingPair, ...]
    exact: tuple[PersistLink, ...]
    candidates: tuple[CandidatePair, ...]
    pending: tuple[CandidatePair, ...]  # candidates awaiting the adjudicator, if none ran
    decisions: tuple[PersistDecision, ...]
    links: tuple[PersistLink, ...]  # selected, sorted by later key
    pair_counts: tuple[PairCounts, ...]
    embedded: int  # distinct factors embedded


@dataclass(frozen=True)
class _PairWork:
    pair: FilingPair
    later: tuple[ResolveNode, ...]
    earlier: tuple[ResolveNode, ...]
    exact: tuple[PersistLink, ...]
    unmatched: tuple[ResolveNode, ...]  # later factors with no exact match


def link_factors(filings: Sequence[Filing], factors: Sequence[ResolveNode],
                 embedder: FactorEmbedder | None, adjudicator: PersistAdjudicator | None,
                 config: PersistConfig, *, workers: int = 1) -> Linking:
    """Link *factors* across the filing pairs of *filings*; see the module docstring."""
    by_filing = _by_filing(factors, {f.accession_no for f in filings})
    work = [_pair_work(pair, by_filing) for pair in filing_pairs(filings)]
    to_embed = {n.key: n for w in work if w.unmatched and w.earlier
                for n in (*w.unmatched, *w.earlier)}
    vectors = _vectors(list(to_embed.values()), embedder)
    candidates = tuple(c for w in work for c in neighbours(w.unmatched, w.earlier, vectors,
                                                           config.top_k))
    exact = tuple(link for w in work for link in w.exact)
    if adjudicator is None:
        decisions: tuple[PersistDecision, ...] = ()
    else:
        by_key = {n.key: n for n in factors}
        decisions = tuple(in_order(
            lambda c: adjudicator.judge(c, by_key[c.later_key], by_key[c.earlier_key]),
            candidates, workers))
    links = select_links(exact, decisions)
    return Linking(
        pairs=tuple(w.pair for w in work), exact=exact, candidates=candidates,
        pending=candidates if adjudicator is None else (), decisions=decisions, links=links,
        pair_counts=tuple(_counts(w, candidates, links) for w in work), embedded=len(to_embed),
    )


def _by_filing(factors: Sequence[ResolveNode], accessions: set[str]
               ) -> dict[str, tuple[ResolveNode, ...]]:
    """The risk factors of each filing, by key; ValueError on a node pairing cannot use."""
    if repeated := [k for k, n in Counter(f.key for f in factors).items() if n > 1]:
        raise ValueError(f"{len(repeated)} keys appear twice, such as {repeated[0]}")
    grouped: dict[str, list[ResolveNode]] = {}
    for f in factors:
        if f.label != RISK_LABEL:
            raise ValueError(f"{f.key} is a {f.label}, not a {RISK_LABEL}")
        filings = {e.accession_no for e in f.evidence}
        if len(filings) != 1:
            raise ValueError(f"{f.key} has evidence from {len(filings)} filings")
        accession = filings.pop()
        if accession not in accessions:
            raise ValueError(f"{f.key} comes from {accession}, which is not in the filing list")
        grouped.setdefault(accession, []).append(f)
    return {a: tuple(sorted(ns, key=lambda n: n.key)) for a, ns in grouped.items()}


def _pair_work(pair: FilingPair, by_filing: dict[str, tuple[ResolveNode, ...]]) -> _PairWork:
    later = by_filing.get(pair.later.accession_no, ())
    earlier = by_filing.get(pair.earlier.accession_no, ())
    matches = exact_matches(later, earlier)
    return _PairWork(pair=pair, later=later, earlier=earlier,
                     exact=tuple(exact_link(n, matches[n.key]) for n in later if n.key in matches),
                     unmatched=tuple(n for n in later if n.key not in matches))


def _vectors(nodes: Sequence[ResolveNode], embedder: FactorEmbedder | None
             ) -> dict[str, Sequence[float]]:
    if not nodes:
        return {}
    if embedder is None:
        raise ValueError("risk factors need embedding, so an embedder is needed")
    vectors = embedder.embed([embed_text(n) for n in nodes])
    if len(vectors) != len(nodes):
        raise ValueError(f"the embedder returned {len(vectors)} vectors for {len(nodes)} texts")
    return {n.key: v for n, v in zip(nodes, vectors, strict=True)}


def _counts(work: _PairWork, candidates: Sequence[CandidatePair],
            links: Sequence[PersistLink]) -> PairCounts:
    later = {n.key for n in work.later}
    linked = [link for link in links if link.later_key in later]
    return PairCounts(
        later_accession=work.pair.later.accession_no,
        earlier_accession=work.pair.earlier.accession_no,
        later_factors=len(work.later), earlier_factors=len(work.earlier),
        exact=len(work.exact), candidates=sum(c.later_key in later for c in candidates),
        linked_exact=sum(link.is_exact for link in linked),
        linked_adjudicated=sum(not link.is_exact for link in linked),
    )
