"""extract_filing(): from a filing's chunks to checked write-path records (Step 8).

Each chunk is sent on its own, so one chunk's reply cannot change another's
output. A chunk whose call fails, whose reply was cut off or refused, or
whose reply is not the JSON asked for becomes a per-chunk failure and the
other chunks still run. The checked output of every chunk is merged per
filing and passed through check_batch() as a dry run, with the chunks and the
structural nodes filer references name counted as present. Any violation there
is a bug in this package's own checks: it is logged as one and kept on the
run, never hidden.

The function calls the injected model and nothing else: no file I/O, no
network of its own.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from extract.check import ChunkCheck, ReplyShapeError, check_reply
from extract.filers import FilingInfo, known_nodes
from extract.merge import merge
from extract.outcomes import ChunkFailure, Conflict, Flag, FlagReason, Reason, Rejection
from extract.prompt import ExtractPrompt
from extract.request import ChunkInput, build_request
from graph.batch import Batch, EdgeRecord, NodeRecord, Violation, check_batch
from retrieve.answer_model import AnswerModel, AnswerModelError, TokenUsage, parse_reply

logger = logging.getLogger(__name__)
_UNUSABLE_STOPS = frozenset({"max_tokens", "refusal"})


@dataclass(frozen=True, slots=True)
class ChunkOutcome:
    """One chunk's call: candidates returned, tokens and whether the cache answered."""

    chunk_id: str
    candidates: int
    usage: TokenUsage | None  # None when the call itself failed
    from_cache: bool
    api_ms: float


@dataclass(frozen=True)
class ExtractionRun:
    """Everything one filing's extraction produced; immutable."""

    accession_no: str
    prompt_version: str
    nodes: tuple[NodeRecord, ...]
    edges: tuple[EdgeRecord, ...]
    rejections: tuple[Rejection, ...]
    flags: tuple[Flag, ...]
    conflicts: tuple[Conflict, ...]
    failures: tuple[ChunkFailure, ...]
    chunks: tuple[ChunkOutcome, ...]
    violations: tuple[Violation, ...]  # check_batch() on the merged records: extractor bugs
    usage: TokenUsage = field(init=False)

    def __post_init__(self) -> None:
        used = [c.usage for c in self.chunks if c.usage is not None]
        object.__setattr__(self, "usage", TokenUsage(
            input_tokens=sum(u.input_tokens for u in used),
            output_tokens=sum(u.output_tokens for u in used),
            cache_read_input_tokens=sum(u.cache_read_input_tokens for u in used),
            cache_creation_input_tokens=sum(u.cache_creation_input_tokens for u in used),
        ))

    @property
    def batch(self) -> Batch:
        return Batch(nodes=self.nodes, edges=self.edges)

    @property
    def complete(self) -> bool:
        """Every chunk extracted and the batch check found nothing."""
        return not self.failures and not self.violations

    @property
    def rejection_counts(self) -> Mapping[Reason, int]:
        return MappingProxyType(dict(Counter(r.reason for r in self.rejections)))

    @property
    def flag_counts(self) -> Mapping[FlagReason, int]:
        return MappingProxyType(dict(Counter(f.reason for f in self.flags)))


def extract_filing(
    filing: FilingInfo, chunks: Sequence[ChunkInput], model: AnswerModel, prompt: ExtractPrompt,
) -> ExtractionRun:
    """Extract every chunk of *filing* through *model* with *prompt*, check and merge."""
    _check_chunks(filing, chunks)
    results = [_extract_chunk(filing, chunk, model, prompt) for chunk in chunks]
    checks = [r for _, r in results if isinstance(r, ChunkCheck)]
    failures = tuple(r for _, r in results if isinstance(r, ChunkFailure))
    merged = merge(checks)
    violations = check_batch(
        Batch(nodes=merged.nodes, edges=merged.edges),
        existing_chunk_ids=frozenset(c.chunk_id for c in chunks),
        existing_nodes=known_nodes(filing),
    )
    if violations:
        logger.error("extractor bug: %d check_batch() violations on checked records of %s; "
                     "first: %s", len(violations), filing.accession_no, violations[0])
    return ExtractionRun(
        accession_no=filing.accession_no,
        prompt_version=prompt.prompt_version,
        nodes=merged.nodes,
        edges=merged.edges,
        rejections=tuple(r for c in checks for r in c.rejections),
        flags=tuple(f for c in checks for f in c.flags),
        conflicts=merged.conflicts,
        failures=failures,
        chunks=tuple(outcome for outcome, _ in results),
        violations=violations,
    )


def _check_chunks(filing: FilingInfo, chunks: Sequence[ChunkInput]) -> None:
    seen: set[str] = set()
    for chunk in chunks:
        if not chunk.chunk_id.startswith(f"{filing.accession_no}:"):
            raise ValueError(f"{chunk.chunk_id} is not a chunk of {filing.accession_no}")
        if chunk.chunk_id in seen:
            raise ValueError(f"{chunk.chunk_id} is listed twice")
        seen.add(chunk.chunk_id)


def _extract_chunk(
    filing: FilingInfo, chunk: ChunkInput, model: AnswerModel, prompt: ExtractPrompt,
) -> tuple[ChunkOutcome, ChunkCheck | ChunkFailure]:
    """One chunk's call and checks; anything unusable becomes a ChunkFailure."""
    try:
        response = model.complete(build_request(prompt, filing, chunk))
    except AnswerModelError as exc:
        return _failed(ChunkOutcome(chunk.chunk_id, 0, None, False, 0.0), str(exc))
    outcome = ChunkOutcome(chunk.chunk_id, 0, None, response.from_cache, response.api_ms)
    try:
        reply = parse_reply(response.body)
    except AnswerModelError as exc:
        return _failed(outcome, str(exc))
    outcome = replace(outcome, usage=reply.usage)
    if reply.stop_reason in _UNUSABLE_STOPS:
        return _failed(outcome, f"the reply stopped with {reply.stop_reason}")
    try:
        parsed = json.loads(reply.text)
    except ValueError:
        return _failed(outcome, "the reply is not valid JSON")
    try:
        check = check_reply(parsed, chunk, filing, prompt.prompt_version)
    except ReplyShapeError as exc:
        return _failed(outcome, str(exc))
    return replace(outcome, candidates=check.candidates), check


def _failed(outcome: ChunkOutcome, reason: str) -> tuple[ChunkOutcome, ChunkFailure]:
    logger.warning("chunk %s failed: %s", outcome.chunk_id, reason)
    return outcome, ChunkFailure(outcome.chunk_id, reason)
