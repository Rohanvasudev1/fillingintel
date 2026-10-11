"""Resolve the extracted nodes of every candidates file and write the outputs (Step 9).

Usage (needs ``ANTHROPIC_API_KEY`` and ``DATABASE_URL``, with
``ANTHROPIC_BASE_URL`` unset; ``VOYAGE_API_KEY`` only when some name embeddings
are not cached yet)::

    uv run --env-file .env python -m resolve.run
    uv run --env-file .env python -m resolve.run --candidates data/extract/<no>-run1.jsonl
    uv run --env-file .env python -m resolve.run --max-cost 5

By default the command reads the latest run of each accession in
``data/extract/``. It embeds the named nodes through the batched Voyage path and
its disk cache (``data/cache/entity_embeddings/``), scores every same-label
pair and makes the deterministic merges. It then reads the evidence chunks of
every other candidate pair from Postgres (the text ``resolve()`` returns),
prints a cost estimate for the adjudicator calls not already in the response
cache, and stops if it is over ``--max-cost`` (default $10). claude-sonnet-5-5
judges each pair, 4 calls at a time, and the `same` decisions are grouped
(``resolve.pipeline``). New merges are appended to
``benchmarks/resolution/merge_log.jsonl``; the alias map (``alias_map.json``)
is rewritten as the replay of the whole log; the run report goes to
``run{N}.json``. A rerun on unchanged inputs replays every reply from the cache
and appends nothing.

With ``PHOENIX_COLLECTOR_ENDPOINT`` set, the run is one trace: a root span and
one LLM span per adjudicator call (``resolve.tracing``). Unset, nothing is
traced.

Exit codes: 0 done; 2 bad input (bad arguments, no candidates file, a
malformed candidates file or merge log); 3 could not run (a setting missing,
``ANTHROPIC_BASE_URL`` set, Postgres unreachable or a chunk missing, the
estimate over ``--max-cost``, a Voyage, cache or file error, or an
adjudicator call failed: the report is then written but nothing is appended
to the log, and a rerun tries again); 4 checks failed (the alias map on disk
is not the replay of the merge log on disk, so one was edited by hand).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg

from ingest.provenance import REPO_ROOT, git_state
from ingest.store import ChunkNotFound, resolve
from ingest.voyage import BatchEmbedder, BatchEmbedding, VoyageClient, VoyageError
from resolve.adjudicate import ClaudeAdjudicator
from resolve.candidates import ScoredPair, is_named
from resolve.config import ResolveConfig
from resolve.decisions import Failure
from resolve.embed import VoyageNodeEmbedder, embed_text
from resolve.estimate import CostEstimate, estimate_cost
from resolve.labeller import LABEL_NOTE
from resolve.labels import LabelFileError, label_status
from resolve.log import LogError, LogEvent, Stamp, append_events, read_log, replay
from resolve.nodes import InputError, InputFile, ResolveNode, load_nodes
from resolve.paths import (
    ALIAS_MAP_NAME,
    CANDIDATES_DIR,
    MERGE_LOG_NAME,
    OUTPUT_DIR,
    latest_candidates,
    next_report,
    read_alias_map,
    write_alias_map,
)
from resolve.pipeline import Resolution, resolve_nodes
from resolve.prompt import PromptError, ResolvePrompt, load_prompt
from resolve.report import AdjudicationInfo, EmbeddingUse, RunInfo, build_report, write_report
from resolve.request import ADJUDICATION_PRICES, ADJUDICATOR_MODEL, sent_chunks
from resolve.tracing import RUN_SPAN, TracedAdjudicatorModel
from retrieve.answer_model import API_KEY_ENV, AnswerModel, AnswerModelError, AnthropicAnswerModel
from retrieve.query_cache import BATCH_CACHE_DIR, CachedBatchEmbedder, CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel
from retrieve.tracing import Kind, SpanRecorder, TracingConfigError, build_provider, text_capture

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_CHECK_FAILED = 4
WORKERS = 4
DEFAULT_MAX_COST_USD = 10.0
DATABASE_ENV = "DATABASE_URL"
BASE_URL_ENV = "ANTHROPIC_BASE_URL"
TRACER_NAME = "filingintel.resolve"

OpenClient = Callable[[], AbstractContextManager[BatchEmbedder]]
OpenModel = Callable[[], AbstractContextManager[AnswerModel]]
ReadTexts = Callable[[str, Sequence[str]], Mapping[str, str]]
Tracing = Callable[[Mapping[str, str]], "TracerProvider | None"]
logger = logging.getLogger(__name__)


class _Stop(Exception):
    """The run stops with *code*; the message is safe to print."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class _NoCalls:
    """Stands in for the Voyage client when every text is cached."""

    def embed_batch(self, texts: Sequence[str], model: str, input_type: str) -> BatchEmbedding:
        raise AssertionError("every text was cached, yet an embedding call was made")


@dataclass(frozen=True)
class _Deps:
    """What main() injects, so tests replace the network, the database and tracing."""

    open_client: OpenClient
    open_model: OpenModel
    read_texts: ReadTexts
    embed_cache: Path
    response_cache: Path


@dataclass(frozen=True)
class _Inputs:
    nodes: tuple[ResolveNode, ...]
    files: tuple[InputFile, ...]
    log: tuple[LogEvent, ...]
    log_path: Path
    map_path: Path
    labels: Mapping[str, object]  # resolve.labels.label_status, for labels_and_scoring


def _cost_cap(value: str) -> float:
    try:
        cap = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not cap >= 0:  # also refuses nan
        raise argparse.ArgumentTypeError("--max-cost must be 0 or more")
    return cap


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m resolve.run", description=__doc__.split(
        "\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", type=Path, nargs="+",
                        help="candidates files (default: the latest run of each accession)")
    parser.add_argument("--max-cost", type=_cost_cap, default=DEFAULT_MAX_COST_USD,
                        help=f"refuse adjudication estimated above this many USD "
                             f"(default {DEFAULT_MAX_COST_USD:g})")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


def _check_env() -> str:
    """The database URL; stops when a setting is missing or ``ANTHROPIC_BASE_URL`` is set."""
    missing = [name for name in (API_KEY_ENV, DATABASE_ENV) if not os.environ.get(name)]
    if missing:
        raise _Stop(EXIT_RUN_ERROR, f"{', '.join(missing)} not set (run with: uv run "
                                    "--env-file .env python -m resolve.run ...)")
    if os.environ.get(BASE_URL_ENV):
        raise _Stop(EXIT_RUN_ERROR, f"{BASE_URL_ENV} is set; unset it so filing text and the "
                                    "API key go only to Anthropic")
    return os.environ[DATABASE_ENV]


def _spans(tracing: Tracing) -> tuple[SpanRecorder, TracerProvider | None]:
    try:
        capture = text_capture(os.environ)
        provider = tracing(os.environ)
    except TracingConfigError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    if provider is None:
        return SpanRecorder.off(), None
    return SpanRecorder(provider.get_tracer(TRACER_NAME), capture), provider


def _voyage_client() -> AbstractContextManager[BatchEmbedder]:
    return VoyageClient.from_env()


def _anthropic_model() -> AbstractContextManager[AnswerModel]:
    return AnthropicAnswerModel.from_env()


def _postgres_texts(url: str, chunk_ids: Sequence[str]) -> dict[str, str]:
    """The text resolve() returns for each chunk, from one connection."""
    with psycopg.connect(url) as conn:
        return {chunk_id: resolve(conn, chunk_id) for chunk_id in chunk_ids}


def main(argv: Sequence[str] | None = None, *, candidates_dir: Path = CANDIDATES_DIR,
         out_dir: Path = OUTPUT_DIR, embed_cache: Path = BATCH_CACHE_DIR,
         open_client: OpenClient = _voyage_client, config: ResolveConfig | None = None,
         open_model: OpenModel = _anthropic_model, read_texts: ReadTexts = _postgres_texts,
         response_cache: Path = DEFAULT_CACHE_DIR, tracing: Tracing = build_provider) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    provider = None
    try:
        args = _parse_args(argv)
        url = _check_env()
        spans, provider = _spans(tracing)
        try:
            prompt = load_prompt()
        except PromptError as exc:
            raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
        inputs = _read_inputs(args.candidates or latest_candidates(candidates_dir), out_dir)
        deps = _Deps(open_client, open_model, read_texts, embed_cache, response_cache)
        return _run(inputs, out_dir, config or ResolveConfig(), prompt, spans, url,
                    args.max_cost, deps)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code
    finally:
        if provider is not None:
            provider.shutdown()  # flushes queued spans


def _read_inputs(files: Sequence[Path], out_dir: Path) -> _Inputs:
    if not files:
        raise _Stop(EXIT_USAGE, "no candidates files; run python -m extract.run first")
    log_path, map_path = out_dir / MERGE_LOG_NAME, out_dir / ALIAS_MAP_NAME
    try:
        nodes, inputs = load_nodes(files)
        log = read_log(log_path)
        on_disk = read_alias_map(map_path)
        replayed = replay(log)
        labels = {**label_status(out_dir), "note": f"{LABEL_NOTE}; scores: python -m "
                                                   "resolve.score"}
    except (InputError, LogError, LabelFileError, ValueError) as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, "cannot read the merge log, alias map or label files: "
                                    f"{exc}") from exc
    if on_disk != replayed:
        raise _Stop(EXIT_CHECK_FAILED, f"{map_path.name} is not the replay of {log_path.name}; "
                                       "one was edited by hand")
    return _Inputs(nodes, inputs, log, log_path, map_path, labels)


def _run(inputs: _Inputs, out_dir: Path, config: ResolveConfig, prompt: ResolvePrompt,
         spans: SpanRecorder, url: str, max_cost: float, deps: _Deps) -> int:
    stamp = Stamp(config.config_hash, git_state(), datetime.now(UTC).isoformat(timespec="seconds"))
    texts = [embed_text(n, config.embed_text) for n in inputs.nodes if is_named(n.label)]
    with _cached_embedder(texts, config, deps.embed_cache, deps.open_client) as cache:
        embedder = VoyageNodeEmbedder(cache, config.embedding_model, config.embed_input_type) \
            if config.top_k else None
        first = _resolve(inputs, embedder, None, config, stamp)
        chunk_texts = _chunk_texts(first.pending, inputs.nodes, url, deps.read_texts)
        with _model(deps.open_model) as inner:
            cached = CachedAnswerModel(inner, deps.response_cache)
            estimate = _check_estimate(ClaudeAdjudicator(cached, prompt, chunk_texts),
                                       cached, first.pending, inputs.nodes, max_cost)
            traced = TracedAdjudicatorModel(cached, spans, prompt.prompt_version,
                                            ADJUDICATION_PRICES)
            with spans.span(RUN_SPAN, Kind.CHAIN, {}):
                result = _resolve(inputs, embedder, ClaudeAdjudicator(traced, prompt, chunk_texts),
                                  config, stamp)
    embedding = EmbeddingUse(len(set(texts)), cache.calls, cache.tokens)
    return _write(inputs, out_dir, result, stamp, config, embedding,
                  AdjudicationInfo(prompt, estimate, WORKERS))


def _resolve(inputs: _Inputs, embedder: VoyageNodeEmbedder | None,
             adjudicator: ClaudeAdjudicator | None, config: ResolveConfig,
             stamp: Stamp) -> Resolution:
    try:
        return resolve_nodes(inputs.nodes, embedder, adjudicator, config, log=inputs.log,
                             stamp=stamp, workers=WORKERS)
    # ValueError: a vector missing, empty or miscounted, or a chunk text missing
    except (VoyageError, CacheError, OSError, ValueError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not resolve: {exc}") from exc


def _chunk_texts(pending: Sequence[ScoredPair], nodes: Sequence[ResolveNode], url: str,
                 read_texts: ReadTexts) -> Mapping[str, str]:
    """The text of every chunk sent for a pending pair's nodes, read from Postgres."""
    by_key = {n.key: n for n in nodes}
    needed = sorted({c for p in pending for k in (p.key_a, p.key_b)
                     for c in sent_chunks(by_key[k])})
    if not needed:
        return {}
    try:
        return read_texts(url, needed)
    except ChunkNotFound as exc:
        raise _Stop(EXIT_RUN_ERROR, f"chunk {exc} is not in Postgres; load the filing with "
                                    "python -m ingest.load") from exc
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise _Stop(EXIT_RUN_ERROR, f"could not read the chunks: database error "
                                    f"({type(exc).__name__})") from exc


@contextmanager
def _model(open_model: OpenModel) -> Iterator[AnswerModel]:
    try:
        client = open_model()
    except AnswerModelError as exc:  # MissingApiKey
        raise _Stop(EXIT_RUN_ERROR, f"cannot open the Anthropic client: {exc}") from exc
    with client as model:
        yield model


def _check_estimate(adjudicator: ClaudeAdjudicator, cached: CachedAnswerModel,
                    pending: Sequence[ScoredPair], nodes: Sequence[ResolveNode],
                    max_cost: float) -> CostEstimate:
    """The estimate for the uncached calls, printed; stops when it is over *max_cost*."""
    by_key = {n.key: n for n in nodes}
    try:
        requests = [adjudicator.request(by_key[p.key_a], by_key[p.key_b]) for p in pending]
    except ValueError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot build the adjudicator requests: {exc}") from exc
    estimate = estimate_cost([r for r in requests if not cached.contains(r)], WORKERS)
    print(f"{len(pending)} candidate pairs to adjudicate, {estimate.calls} not cached; "
          f"estimate ${estimate.usd:.2f} (cap ${max_cost:.2f}, {ADJUDICATOR_MODEL}, "
          f"{estimate.price_table_date} prices)")
    if estimate.usd > max_cost:
        raise _Stop(EXIT_RUN_ERROR, f"the estimate ${estimate.usd:.2f} is over --max-cost "
                                    f"${max_cost:.2f}; no adjudicator call was made")
    return estimate


def _write(inputs: _Inputs, out_dir: Path, result: Resolution, stamp: Stamp,
           config: ResolveConfig, embedding: EmbeddingUse, adjudication: AdjudicationInfo) -> int:
    failed = sum(d.failure == Failure.CALL_FAILED for d in result.decisions)
    try:
        if not failed:
            append_events(inputs.log_path, result.merges)
        aliases = replay(read_log(inputs.log_path))
        write_alias_map(inputs.map_path, aliases)
        report_path = next_report(out_dir)
        info = RunInfo(run=int(report_path.stem.removeprefix("run")), stamp=stamp, config=config,
                       inputs=inputs.files, embedding=embedding, alias_map_keys=len(aliases),
                       adjudication=adjudication, complete=not failed, labels=inputs.labels)
        write_report(report_path, build_report(result, info))
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write the outputs: {exc}") from exc
    print(_summary(inputs.nodes, result, [inputs.log_path, inputs.map_path, report_path]))
    if failed:
        print(f"{failed} adjudicator calls failed; nothing was appended to the merge log. "
              "Rerun to try them again.", file=sys.stderr)
        return EXIT_RUN_ERROR
    return EXIT_OK


@contextmanager
def _cached_embedder(texts: Sequence[str], config: ResolveConfig, cache_dir: Path,
                     open_client: OpenClient) -> Iterator[CachedBatchEmbedder]:
    """The cached embedder, holding a Voyage client only when some text is uncached."""
    offline = CachedBatchEmbedder(_NoCalls(), cache_dir)
    missing = offline.uncached(texts, config.embedding_model, config.embed_input_type) \
        if config.top_k else []
    if not missing:
        yield offline
        return
    logger.info("%d names to embed, %d cached", len(missing), len(set(texts)) - len(missing))
    try:
        client = open_client()
    except VoyageError as exc:
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
    with client as voyage:
        yield CachedBatchEmbedder(voyage, cache_dir)


def _summary(nodes: Sequence[ResolveNode], result: Resolution, paths: Sequence[Path]) -> str:
    shown = ", ".join(_display(p) for p in paths)
    return (f"{len(nodes)} nodes, {sum(p.is_candidate for p in result.pairs)} candidate pairs, "
            f"{len(result.decisions)} adjudicated, {len(result.held_back)} groups held back, "
            f"{len(result.merges)} new merges ({len(result.proposed)} in all); wrote {shown}")


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
