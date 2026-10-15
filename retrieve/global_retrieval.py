"""graph_global's retrieval: communities and the chunks shown for them (Step 12, ADR-0008).

``GlobalSelection.retrieve`` filters the question (ADR-0001), embeds it, picks
the top N communities of one level by report cosine with evidence in the
filter (``retrieve.community_select``), and ranks each one's filtered evidence
chunks by cosine against ``chunk_embeddings``, keeping ``CHUNKS_PER_COMMUNITY``.  The ranked list is
the shown chunks by community rank, then cosine, each once.  The map and
reduce calls (ticket 06) build on this; ``python -m eval.global_n`` scores it
alone.

A community's evidence is its members' ``EVIDENCED_BY`` chunks: a lower
community's own members, a top community's through its children.  Its report
text comes from the reports file the latest graph load names
(``loaded_reports_file``), and must carry the member hash the graph holds.

Before the first question the selection checks ``:GraphMeta`` and that every
report text has a cached embedding, so it never embeds a report itself;
``python -m retrieve.node_embed`` does that, with its estimate first.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg
from neo4j import Driver, ManagedTransaction, unit_of_work

from graph.batch import CHUNK, EVIDENCED_BY
from graph.community import LOWER, TOP, Level
from graph.connection import DATABASE, WHOLE_GRAPH_TIMEOUT_S, read_session
from graph.cypher import quoted
from graph.load import COMMUNITIES_DIR
from graph.load import OUTPUT_DIR as LOAD_REPORT_DIR
from graph.load_report import latest_load_run
from graph.ontology import COMMUNITY, IN_COMMUNITY, PARENT_OF
from graph.report_file import ReportEntry, ReportsFile, ReportsFileError, read_reports_file
from ingest.store import chunk_cosines
from ingest.voyage import DEFAULT_MODEL, QueryEmbedder, VoyageError
from retrieve.arm import ArmError, Retrieval, RetrievedChunk
from retrieve.arm_steps import arm_error, corpus_filings, embed_question
from retrieve.community_select import (
    CHUNKS_PER_COMMUNITY,
    CommunityIndex,
    ReportedCommunity,
    ranked_list,
    report_text,
    shown_chunks,
)
from retrieve.graph_local import GRAPH_ERRORS, graph_provenance
from retrieve.graph_nodes import NODE_INPUT_TYPE
from retrieve.pricing import embedding_cost
from retrieve.query_cache import CachedBatchEmbedder, CacheError
from retrieve.question_filter import companies_without_chunks, parse_question_filter
from retrieve.tracing import SpanRecorder

ARM_NAME = "graph_global"
# The choices eval.global_n scores, and the one chosen on dev retrieval-only recall@10
# (Step 12 ticket 05, benchmarks/graph/global-n-run1.json).
N_CHOICES = (4, 6, 8, 12)
COMMUNITIES_N = 4
_MS_PER_SECOND = 1000.0
# ValueError: a zero vector, or a community chunk with no vector.
RETRIEVAL_ERRORS = (VoyageError, CacheError, OSError, ValueError, psycopg.Error, *GRAPH_ERRORS)

_C, _IN, _PARENT = quoted(COMMUNITY), quoted(IN_COMMUNITY), quoted(PARENT_OF)
_CHUNK, _EVIDENCE = quoted(CHUNK), quoted(EVIDENCED_BY)
# The members whose evidence a community of each level holds.
_MEMBER_PATHS = {
    LOWER: f"(c)<-[:{_IN}]-(m)",
    TOP: f"(c)-[:{_PARENT}]->(:{_C})<-[:{_IN}]-(m)",
}


@dataclass(frozen=True)
class ShownCommunity:
    """A selected community and the chunks a map call would be shown for it."""

    community_id: str
    rank: int  # 1 for the best
    similarity: float
    chunks: tuple[RetrievedChunk, ...]  # best first


@dataclass(frozen=True)
class GlobalRetrieval:
    retrieval: Retrieval  # retrieved: the shown chunks by community rank, then cosine
    communities: tuple[ShownCommunity, ...]

    def ranked_ids(self, n: int) -> list[str]:
        """The ranked list's chunk IDs had only the first *n* communities been selected."""
        return [c.chunk_id for c in ranked_list(c.chunks for c in self.communities[:n])]


class GlobalSelection:
    """Community selection and chunk ranking over one graph, one level and one N."""

    def __init__(
        self,
        conn: psycopg.Connection,
        driver: Driver,
        embedder: QueryEmbedder,
        report_embeddings: CachedBatchEmbedder,
        reports: ReportsFile,
        *,
        n: int = COMMUNITIES_N,
        level: Level = LOWER,
        per_community: int = CHUNKS_PER_COMMUNITY,
        model: str = DEFAULT_MODEL,
        clock: Callable[[], float] = time.perf_counter,
        spans: SpanRecorder | None = None,
        load_reports: Path = LOAD_REPORT_DIR,
        database: str = DATABASE,
    ):
        self._conn = conn
        self._embedder = embedder
        self._n = n
        self._per_community = per_community
        self._model = model
        self._clock = clock
        self._spans = spans if spans is not None else SpanRecorder.off()
        try:
            self.provenance = graph_provenance(driver, database, load_reports)
            communities = read_reported_communities(driver, level, reports, database)
            self._index = community_index(communities, report_embeddings, model)
        except (*GRAPH_ERRORS, CacheError, OSError, ValueError) as exc:
            raise arm_error(ARM_NAME, exc) from exc
        self._filings = corpus_filings(conn)

    def retrieve(self, question: str) -> GlobalRetrieval:
        """Filter, embed, select and rank for one question; failures raise ``ArmError``."""
        question_filter = parse_question_filter(question, self._filings)
        try:
            start = self._clock()
            embedding = embed_question(self._embedder, self._model, question, self._spans)
            embedded = self._clock()
            selected = self._index.select(embedding.vector, question_filter.accession_nos,
                                          self._n)
            scores = chunk_cosines(self._conn, self._model, embedding.vector,
                                   {c for s in selected for c in s.chunk_ids})
            communities = tuple(
                ShownCommunity(s.community_id, rank, s.similarity,
                               shown_chunks(s.chunk_ids, scores, self._per_community))
                for rank, s in enumerate(selected, start=1))
            searched = self._clock()
        except RETRIEVAL_ERRORS as exc:
            raise arm_error(ARM_NAME, exc) from exc
        retrieved = ranked_list(c.chunks for c in communities)
        retrieval = Retrieval(
            retrieved=retrieved,
            question_filter=question_filter,
            companies_without_chunks=companies_without_chunks(
                question_filter, (r.chunk_id for r in retrieved), self._filings),
            embed_ms=(embedded - start) * _MS_PER_SECOND,
            search_ms=(searched - embedded) * _MS_PER_SECOND,
            query_cached=embedding.from_cache,
            embed_tokens=embedding.api_token_count,
            embed_cost_usd=embedding_cost(self._model, embedding.api_token_count),
        )
        return GlobalRetrieval(retrieval, communities)


def read_reported_communities(driver: Driver, level: Level, reports: ReportsFile,
                              database: str = DATABASE) -> tuple[ReportedCommunity, ...]:
    """The graph's *level* communities that have a report, by community_id.

    Raises ``ValueError`` when one has no report in *reports*, or one written for
    another member hash or with another text than the graph holds.
    """
    with read_session(driver, database) as session:
        rows = session.execute_read(
            unit_of_work(timeout=WHOLE_GRAPH_TIMEOUT_S)(_read_communities), level)
    communities = []
    for community_id, graph_hash, graph_report, chunks in rows:
        entry = reports.entries.get(community_id)
        problem = _report_problem(entry, graph_hash, graph_report)
        if problem:
            raise ValueError(f"community {community_id} has {problem} in {reports.name}; "
                             "the graph and the reports file disagree")
        communities.append(ReportedCommunity(
            community_id, report_text(entry.title, entry.summary), dict(chunks)))
    return tuple(communities)


def _report_problem(entry: ReportEntry | None, graph_hash: str, graph_report: str) -> str | None:
    """Why *entry* is not the report the graph holds, or None when it is."""
    if entry is None:
        return "no report"
    if entry.member_hash != graph_hash:
        return "a report for another member hash"
    if entry.report != graph_report:
        return "another report text than the graph holds"
    return None


def _read_communities(tx: ManagedTransaction, level: Level
                      ) -> list[tuple[str, str, str, list[list[str]]]]:
    query = (f"MATCH (c:{_C}) WHERE c.level = $level AND c.report IS NOT NULL "
             "RETURN c.community_id AS id, c.member_hash AS member_hash, c.report AS report, "
             f"COLLECT {{ MATCH {_MEMBER_PATHS[level]}-[:{_EVIDENCE}]->(k:{_CHUNK}) "
             "RETURN DISTINCT [k.chunk_id, k.accession_no] } AS chunks ORDER BY id")
    return [(r["id"], r["member_hash"], r["report"], r["chunks"])
            for r in tx.run(query, level=level)]


def community_index(communities: Sequence[ReportedCommunity],
                    report_embeddings: CachedBatchEmbedder, model: str) -> CommunityIndex:
    """The communities with their cached report vectors; ``ArmError`` if one is missing."""
    texts = list(dict.fromkeys(c.text for c in communities))
    missing = report_embeddings.uncached(texts, model, NODE_INPUT_TYPE)
    if missing:
        raise ArmError(f"{len(missing)} report texts have no cached {model} embedding; "
                       "run python -m retrieve.node_embed")
    vectors = report_embeddings.embed(texts, model, NODE_INPUT_TYPE).vectors
    return CommunityIndex(communities, dict(zip(texts, vectors, strict=True)))


def loaded_reports_file(load_reports: Path = LOAD_REPORT_DIR,
                        communities_dir: Path = COMMUNITIES_DIR) -> ReportsFile:
    """The reports file the latest graph load wrote, checked by its SHA-256.

    Raises ``ArmError`` when there is no load report, the load wrote no
    communities, or the file is unreadable or changed since the load.
    """
    run = latest_load_run(load_reports)
    if run is None:
        raise ArmError(f"no graph load report in {load_reports}; run python -m graph.load")
    try:
        loaded = _loaded_communities(load_reports / f"{run}.json")
        path = communities_dir / loaded["reports_file"]
        raw = path.read_bytes()
        reports = read_reports_file(path)
    except (OSError, ValueError, KeyError, TypeError, ReportsFileError) as exc:
        raise ArmError(f"cannot read the reports file {run} loaded: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != loaded["reports_sha256"]:
        raise ArmError(f"{path.name} has changed since {run} loaded it; "
                       "run python -m graph.load --rebuild")
    return reports


def _loaded_communities(path: Path) -> Mapping[str, str]:
    loaded = json.loads(path.read_text(encoding="utf-8")).get("communities")
    if not loaded:
        raise ArmError(f"{path.name} loaded no communities; run python -m graph.load --rebuild")
    return loaded
