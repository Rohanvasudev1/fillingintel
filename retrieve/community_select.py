"""Choosing communities and their chunks for graph_global (Step 12, ADR-0008).

Each community with a report is embedded once as its report text, "title:
summary" (``report_text``).  The question's query embedding is compared with
every report's by exact cosine (ADR-0002: no index).  ``CommunityIndex.select``
keeps, best first with ties by ``community_id``, the top N communities whose
members have evidence chunks in the filings the question filter selected (any
filing with no filter), each with those chunks.

``shown_chunks`` ranks one community's chunks by their cosine with the query,
ties by chunk ID, and cuts them to the per-call limit; ``ranked_list`` joins
the communities' shown chunks by community rank, then cosine, dropping repeats.
That list is what retrieval metrics score.

Everything here is pure: the graph, the reports file and Postgres are read in
``retrieve.global_retrieval``.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from graph.report_file import ReportsFile
from retrieve.anchors import dot, unit_vector
from retrieve.arm import RetrievedChunk

CHUNKS_PER_COMMUNITY = 6


@dataclass(frozen=True)
class ReportedCommunity:
    """A community that has a report, as selection needs it."""

    community_id: str
    text: str  # what its report is embedded as
    chunks: Mapping[str, str]  # its members' evidence: chunk_id -> accession_no


@dataclass(frozen=True)
class SelectedCommunity:
    community_id: str
    similarity: float  # cosine between the query and the report text
    chunk_ids: tuple[str, ...]  # its evidence chunks inside the filter, sorted


def report_text(title: str, summary: str) -> str:
    """The text a community report is embedded as."""
    return f"{title}: {summary}"


def report_texts(reports: ReportsFile) -> list[str]:
    """The text of every report that kept text, in file order.

    A report that kept no text is loaded with none and never selected.
    """
    return [report_text(e.title, e.summary) for e in reports.entries.values() if e.report]


class CommunityIndex:
    """Every community with a report and its report's unit vector, for exact cosine."""

    def __init__(self, communities: Sequence[ReportedCommunity],
                 vectors: Mapping[str, Sequence[float]]):
        missing = sorted({c.text for c in communities} - set(vectors))
        if missing:
            raise ValueError(f"{len(missing)} report texts have no vector, e.g. {missing[0]!r}")
        self._communities = tuple(communities)
        self._units = {c.text: unit_vector(vectors[c.text]) for c in communities}

    def __len__(self) -> int:
        return len(self._communities)

    def select(self, query_vector: Sequence[float], accession_nos: Collection[str] | None,
               n: int) -> tuple[SelectedCommunity, ...]:
        """The top *n* communities with evidence in *accession_nos* (None: any filing)."""
        allowed = None if accession_nos is None else frozenset(accession_nos)
        query = unit_vector(query_vector)
        found = []
        for community in self._communities:
            chunk_ids = tuple(sorted(c for c, a in community.chunks.items()
                                     if allowed is None or a in allowed))
            if chunk_ids:
                similarity = dot(query, self._units[community.text])
                found.append(SelectedCommunity(community.community_id, similarity, chunk_ids))
        found.sort(key=lambda s: (-s.similarity, s.community_id))
        return tuple(found[:n])


def shown_chunks(chunk_ids: Iterable[str], scores: Mapping[str, float],
                 limit: int = CHUNKS_PER_COMMUNITY) -> tuple[RetrievedChunk, ...]:
    """The *limit* chunks with the highest cosine, ties by chunk ID.

    Raises ``ValueError`` for a chunk *scores* has no cosine for.
    """
    ids = tuple(chunk_ids)
    missing = sorted(set(ids) - set(scores))
    if missing:
        raise ValueError(f"{len(missing)} community chunks have no vector, e.g. {missing[0]}")
    ranked = sorted(ids, key=lambda c: (-scores[c], c))
    return tuple(RetrievedChunk(c, scores[c]) for c in ranked[:limit])


def ranked_list(shown: Iterable[Sequence[RetrievedChunk]]) -> tuple[RetrievedChunk, ...]:
    """The communities' shown chunks by community rank, then cosine, each chunk once."""
    seen: dict[str, RetrievedChunk] = {}
    for chunks in shown:
        for chunk in chunks:
            seen.setdefault(chunk.chunk_id, chunk)
    return tuple(seen.values())
