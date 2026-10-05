"""Every chunk of the corpus, in memory, for authoring and validating eval sets.

Built from ``data/parsed`` with the same chunker the loader uses, so chunk
IDs and text match Postgres and ``resolve()`` exactly, without a database.
Chunking 24 filings takes ~20s, so chunk offsets are cached next to the
parsed files, keyed by each file's hash and the chunker version.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ingest.chunker import CHUNKER_VERSION, chunk_filing
from ingest.corpus import TICKER_BY_CIK
from ingest.parsed_files import PARSED_SUFFIX, ParsedRecord, read_parsed, read_parsed_dir

logger = logging.getLogger(__name__)

CACHE_NAME = ".chunk_index.cache"  # not *.json: read_parsed_dir globs those
_WORD = re.compile(r"[a-z0-9]+")
_SNIPPET_CHARS = 160


@dataclass(frozen=True)
class ChunkInfo:
    accession_no: str
    ticker: str
    form_type: str
    fiscal_period: str
    section: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: int
    snippet: str


class ChunkIndex:
    """Chunk ID → text and labels, plus keyword search."""

    def __init__(self, texts: dict[str, str], chunks: dict[str, ChunkInfo]) -> None:
        self._texts = texts  # accession_no -> full parsed text
        self._chunks = chunks  # chunk_id -> ChunkInfo, in document order

    # ── construction ──────────────────────────────────────────────────────────

    @classmethod
    def from_records(cls, records: list[ParsedRecord]) -> ChunkIndex:
        texts: dict[str, str] = {}
        chunks: dict[str, ChunkInfo] = {}
        for record in records:
            texts[record.meta.accession_no] = record.filing.text
            chunks.update(_chunk_infos(record))
        return cls(texts, chunks)

    @classmethod
    def from_parsed_dir(cls, parsed_dir: Path) -> ChunkIndex:
        """Load ``parsed_dir``; reuse cached chunk offsets when files and chunker are unchanged."""
        paths = sorted(parsed_dir.glob(f"*{PARSED_SUFFIX}"))
        if not paths:
            raise FileNotFoundError(f"no parsed filings in {parsed_dir}")
        key = _cache_key(paths)
        cached = _read_cache(parsed_dir / CACHE_NAME, key)
        if cached is None:
            index = cls.from_records(read_parsed_dir(parsed_dir))
            _write_cache(parsed_dir / CACHE_NAME, key, index._chunks)
            return index
        texts = {p.stem: read_parsed(p).filing.text for p in paths}
        return cls(texts, cached)

    # ── lookups ───────────────────────────────────────────────────────────────

    def chunk_ids(self) -> list[str]:
        return list(self._chunks)

    def info(self, chunk_id: str) -> ChunkInfo | None:
        return self._chunks.get(chunk_id)

    def text(self, chunk_id: str) -> str | None:
        """Exact chunk text (what ``resolve()`` returns), or None for an unknown ID."""
        info = self._chunks.get(chunk_id)
        if info is None:
            return None
        return self._texts[info.accession_no][info.char_start : info.char_end]

    def ticker(self, chunk_id: str) -> str | None:
        info = self._chunks.get(chunk_id)
        return None if info is None else info.ticker

    def fiscal_period(self, chunk_id: str) -> str | None:
        info = self._chunks.get(chunk_id)
        return None if info is None else info.fiscal_period

    def section(self, chunk_id: str) -> str | None:
        info = self._chunks.get(chunk_id)
        return None if info is None else info.section

    # ── search ────────────────────────────────────────────────────────────────

    def search(
        self,
        terms: str,
        ticker: str | None = None,
        fiscal_period: str | None = None,
        section: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        """Chunks containing every term as a word prefix (case-insensitive), most matches first.

        "tax" matches "taxes" and "taxonomy"; "10" matches "100".
        """
        words = _WORD.findall(terms.lower())
        hits: list[SearchHit] = []
        for chunk_id, info in self._chunks.items():
            if not _matches(info, ticker, fiscal_period, section):
                continue
            text = self._texts[info.accession_no][info.char_start : info.char_end]
            lowered = text.lower()
            counts = [_count(lowered, w) for w in words]
            if words and all(counts):
                hits.append(SearchHit(chunk_id, sum(counts), _snippet(text, words[0])))
        return sorted(hits, key=lambda h: (-h.score, h.chunk_id))[:limit]


def _chunk_infos(record: ParsedRecord) -> dict[str, ChunkInfo]:
    ticker = TICKER_BY_CIK.get(record.meta.cik, record.meta.cik)
    return {
        c.chunk_id: ChunkInfo(
            accession_no=c.accession_no,
            ticker=ticker,
            form_type=c.form_type,
            fiscal_period=c.fiscal_period,
            section=c.section,
            char_start=c.char_start,
            char_end=c.char_end,
        )
        for c in chunk_filing(record.filing)
    }


def _matches(info: ChunkInfo, ticker: str | None, period: str | None, section: str | None) -> bool:
    return (
        (ticker is None or info.ticker == ticker.upper())
        and (period is None or info.fiscal_period == period)
        and (section is None or info.section == section)
    )


def _count(text: str, word: str) -> int:
    return len(re.findall(rf"\b{re.escape(word)}", text))


def _snippet(text: str, word: str) -> str:
    pos = max(text.lower().find(word), 0)
    start = max(pos - _SNIPPET_CHARS // 2, 0)
    return " ".join(text[start : start + _SNIPPET_CHARS].split())


def _cache_key(paths: list[Path]) -> str:
    digest = hashlib.sha256(CHUNKER_VERSION.encode())
    for path in paths:
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _read_cache(path: Path, key: str) -> dict[str, ChunkInfo] | None:
    """Cached offsets, or None if the cache is missing, stale or malformed (a miss)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") != key:
            return None
        return {chunk_id: ChunkInfo(**fields) for chunk_id, fields in data["chunks"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _write_cache(path: Path, key: str, chunks: dict[str, ChunkInfo]) -> None:
    """Write the cache through a unique temporary file; a failed write is only a lost cache."""
    payload = {"key": key, "chunks": {cid: vars(info) for cid, info in chunks.items()}}
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f"{path.name}.", delete=False
        ) as fh:
            fh.write(json.dumps(payload))
        os.replace(fh.name, path)
    except OSError as exc:
        logger.warning("could not write the chunk-index cache %s: %s", path, exc)
