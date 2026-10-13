"""Link each risk factor to the same risk in the company's earlier 10-K (Step 10).

Usage (needs ``ANTHROPIC_API_KEY``, ``VOYAGE_API_KEY`` and ``DATABASE_URL``,
with ``ANTHROPIC_BASE_URL`` unset)::

    uv run --env-file .env python -m persist.run
    uv run --env-file .env python -m persist.run --max-cost 1.5

The command reads the risk factors from the latest run of each accession in
``data/extract/`` and the filing list from Postgres, and pairs each 10-K with
the company's previous 10-K and each 10-Q with the most recent 10-K before it
(``persist.filings``). Identical normalized titles link with no call. Every
other later risk factor and the earlier 10-K's factors are embedded through
the batched Voyage path and its disk cache (``data/cache/entity_embeddings/``),
and each such later factor's top 3 earlier neighbours go to claude-haiku-5-5
with prompt ``persist/v1``, 4 calls at a time, through the response cache.
Before any call it prints the estimate for the calls not cached, and stops if
that is over ``--max-cost`` (default and most $2).

A complete run whose links pass ``check_batch()`` as PERSISTS_AS edges
rewrites ``benchmarks/persistence/links.jsonl``; every run writes
``run{N}.json`` beside it. A rerun on unchanged inputs replays every reply and
embedding from the caches.

Exit codes: 0 done; 2 bad input (bad arguments, no candidates file, a
malformed candidates file); 3 could not run (a setting missing,
``ANTHROPIC_BASE_URL`` set, Postgres unreachable or a chunk missing, the
estimate over ``--max-cost``, a Voyage, cache or file error, or an adjudicator
call failed: the report is then written but the link file is kept as it was,
and a rerun tries again); 4 checks failed (a link fails ``check_batch()``;
the report lists the violations and the link file is kept as it was).
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from ingest.provenance import REPO_ROOT, git_state
from ingest.store import ChunkNotFound
from ingest.voyage import API_KEY_ENV as VOYAGE_KEY_ENV
from ingest.voyage import BatchEmbedder, BatchEmbedding, VoyageClient, VoyageError
from persist.adjudicate import ClaudePersistAdjudicator
from persist.config import PersistConfig
from persist.db import postgres_filings, postgres_texts
from persist.decisions import Failure
from persist.filings import Filing
from persist.links import check_links
from persist.paths import LINKS_NAME, OUTPUT_DIR, write_links
from persist.pipeline import Linking, link_factors
from persist.prompt import PersistPrompt, PromptError, load_prompt
from persist.report import EmbeddingUse, RunInfo, build_report, write_report
from persist.request import ADJUDICATOR_MODEL, sent_chunks
from resolve.cli import cost_cap
from resolve.estimate import CostEstimate, estimate_cost
from resolve.nodes import RISK_LABEL, InputError, InputFile, ResolveNode, load_nodes
from resolve.paths import CANDIDATES_DIR, latest_candidates, next_report
from retrieve.answer_model import API_KEY_ENV, AnswerModel, AnswerModelError, AnthropicAnswerModel
from retrieve.query_cache import BATCH_CACHE_DIR, CachedBatchEmbedder, CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_CHECK_FAILED = 4
WORKERS = 4
MAX_COST_USD = 2.0  # the Step 10 cap on persistence adjudication
DATABASE_ENV = "DATABASE_URL"
BASE_URL_ENV = "ANTHROPIC_BASE_URL"

OpenClient = Callable[[], AbstractContextManager[BatchEmbedder]]
OpenModel = Callable[[], AbstractContextManager[AnswerModel]]
ReadFilings = Callable[[str], Sequence[Filing]]
ReadTexts = Callable[[str, Sequence[str]], Mapping[str, str]]
logger = logging.getLogger(__name__)


class _Stop(Exception):
    """The run stops with *code*; the message is safe to print."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Deps:
    """What main() injects, so tests replace the network and the database."""

    open_client: OpenClient
    open_model: OpenModel
    read_texts: ReadTexts
    embed_cache: Path
    response_cache: Path


class _LazyVoyage:
    """A BatchEmbedder that opens the Voyage client on its first call only, so a run whose
    embeddings are all cached never opens it."""

    def __init__(self, open_client: OpenClient, stack: ExitStack):
        self._open = open_client
        self._stack = stack
        self._client: BatchEmbedder | None = None

    def embed_batch(self, texts: Sequence[str], model: str, input_type: str) -> BatchEmbedding:
        if self._client is None:
            self._client = self._stack.enter_context(self._open())
        return self._client.embed_batch(texts, model, input_type)


class _CachedEmbedder:
    """persist.pipeline's embedder seam over the cached batch path."""

    def __init__(self, cache: CachedBatchEmbedder, config: PersistConfig):
        self._cache = cache
        self._config = config

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self._cache.embed(texts, self._config.embedding_model,
                                 self._config.embed_input_type).vectors


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m persist.run", description=__doc__.split(
        "\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", type=Path, nargs="+",
                        help="candidates files (default: the latest run of each accession)")
    parser.add_argument("--max-cost", type=cost_cap, default=MAX_COST_USD,
                        help=f"refuse adjudication estimated above this many USD "
                             f"(default and most {MAX_COST_USD:g})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc
    if args.max_cost > MAX_COST_USD:
        raise _Stop(EXIT_USAGE, f"--max-cost may not exceed the ${MAX_COST_USD:g} cap")
    return args


def _check_env() -> str:
    """The database URL; stops when a setting is missing or ``ANTHROPIC_BASE_URL`` is set."""
    missing = [n for n in (API_KEY_ENV, VOYAGE_KEY_ENV, DATABASE_ENV) if not os.environ.get(n)]
    if missing:
        raise _Stop(EXIT_RUN_ERROR, f"{', '.join(missing)} not set (run with: uv run "
                                    "--env-file .env python -m persist.run ...)")
    if os.environ.get(BASE_URL_ENV):
        raise _Stop(EXIT_RUN_ERROR, f"{BASE_URL_ENV} is set; unset it so filing text and the "
                                    "API key go only to Anthropic")
    return os.environ[DATABASE_ENV]


def _voyage_client() -> AbstractContextManager[BatchEmbedder]:
    return VoyageClient.from_env()


def _anthropic_model() -> AbstractContextManager[AnswerModel]:
    return AnthropicAnswerModel.from_env()


def main(argv: Sequence[str] | None = None, *, candidates_dir: Path = CANDIDATES_DIR,
         out_dir: Path = OUTPUT_DIR, embed_cache: Path = BATCH_CACHE_DIR,
         response_cache: Path = DEFAULT_CACHE_DIR, open_client: OpenClient = _voyage_client,
         open_model: OpenModel = _anthropic_model,
         read_filings: ReadFilings = postgres_filings, read_texts: ReadTexts = postgres_texts,
         config: PersistConfig | None = None) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        args = _parse_args(argv)
        url = _check_env()
        try:
            prompt = load_prompt()
        except PromptError as exc:
            raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
        factors, inputs = _read_factors(args.candidates or latest_candidates(candidates_dir))
        filings = _filings(url, read_filings)
        deps = _Deps(open_client, open_model, read_texts, embed_cache, response_cache)
        return _run(factors, inputs, filings, out_dir, config or PersistConfig(), prompt, url,
                    args.max_cost, deps)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def _read_factors(files: Sequence[Path]) -> tuple[tuple[ResolveNode, ...], tuple[InputFile, ...]]:
    if not files:
        raise _Stop(EXIT_USAGE, "no candidates files; run python -m extract.run first")
    try:
        nodes, inputs = load_nodes(files)
    except InputError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    return tuple(n for n in nodes if n.label == RISK_LABEL), inputs


def _filings(url: str, read_filings: ReadFilings) -> tuple[Filing, ...]:
    try:
        return tuple(read_filings(url))
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise _Stop(EXIT_RUN_ERROR, f"could not read the filings: database error "
                                    f"({type(exc).__name__})") from exc


def _run(factors: Sequence[ResolveNode], inputs: tuple[InputFile, ...],
         filings: Sequence[Filing], out_dir: Path, config: PersistConfig, prompt: PersistPrompt,
         url: str, max_cost: float, deps: _Deps) -> int:
    commit, created_at = git_state(), datetime.now(UTC).isoformat(timespec="seconds")
    with ExitStack() as stack:
        cache = CachedBatchEmbedder(_LazyVoyage(deps.open_client, stack), deps.embed_cache)
        embedder = _CachedEmbedder(cache, config)
        first = _link(filings, factors, embedder, None, config)
        by_key = {f.key: f for f in factors}
        chunk_texts = _chunk_texts(first, by_key, url, deps.read_texts)
        with _model(deps.open_model) as inner:
            cached = CachedAnswerModel(inner, deps.response_cache)
            adjudicator = ClaudePersistAdjudicator(cached, prompt, chunk_texts)
            estimate = _check_estimate(adjudicator, cached, first, by_key, max_cost)
            result = _link(filings, factors, embedder, adjudicator, config)
    info = RunInfo(run=0, commit=commit, created_at=created_at, config=config, prompt=prompt,
                   inputs=inputs, filings=len(filings),
                   embedding=EmbeddingUse(result.embedded, cache.calls, cache.tokens),
                   estimate=estimate, workers=WORKERS, links_sha256=None, complete=True)
    return _write(result, out_dir, info)


def _link(filings: Sequence[Filing], factors: Sequence[ResolveNode], embedder: _CachedEmbedder,
          adjudicator: ClaudePersistAdjudicator | None, config: PersistConfig) -> Linking:
    try:
        return link_factors(filings, factors, embedder, adjudicator, config, workers=WORKERS)
    # ValueError: a factor outside the filing list, a vector missing, or a chunk text missing
    except (VoyageError, CacheError, OSError, ValueError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not link: {exc}") from exc


def _chunk_texts(first: Linking, by_key: Mapping[str, ResolveNode], url: str,
                 read_texts: ReadTexts) -> Mapping[str, str]:
    """The text of every chunk sent for a candidate pair, read from Postgres."""
    needed = sorted({c for p in first.pending for k in (p.later_key, p.earlier_key)
                     for c in sent_chunks(by_key[k])})
    if not needed:
        return {}
    try:
        return read_texts(url, needed)
    except ChunkNotFound as exc:
        raise _Stop(EXIT_RUN_ERROR, f"chunk {exc} is not in Postgres; load the filing with "
                                    "python -m ingest.load") from exc
    except psycopg.Error as exc:
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


def _check_estimate(adjudicator: ClaudePersistAdjudicator, cached: CachedAnswerModel,
                    first: Linking, by_key: Mapping[str, ResolveNode],
                    max_cost: float) -> CostEstimate:
    """The estimate for the uncached calls, printed; stops when it is over *max_cost*."""
    try:
        requests = [adjudicator.request(by_key[p.later_key], by_key[p.earlier_key])
                    for p in first.pending]
    except ValueError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot build the adjudicator requests: {exc}") from exc
    estimate = estimate_cost([r for r in requests if not cached.contains(r)], WORKERS)
    print(f"{len(first.pairs)} filing pairs, {len(first.exact)} exact-title links, "
          f"{len(first.pending)} candidate pairs to adjudicate, {estimate.calls} not cached; "
          f"estimate ${estimate.usd:.2f} (cap ${max_cost:.2f}, {ADJUDICATOR_MODEL}, "
          f"{estimate.price_table_date} prices)")
    if estimate.usd > max_cost:
        raise _Stop(EXIT_RUN_ERROR, f"the estimate ${estimate.usd:.2f} is over --max-cost "
                                    f"${max_cost:.2f}; no adjudicator call was made")
    return estimate


def _write(result: Linking, out_dir: Path, info: RunInfo) -> int:
    failed = sum(d.failure == Failure.CALL_FAILED for d in result.decisions)
    violations = check_links(result.links)
    links_path = out_dir / LINKS_NAME
    try:
        sha = None
        if not failed and not violations:
            write_links(links_path, result.links)
            sha = hashlib.sha256(links_path.read_bytes()).hexdigest()
        report_path = next_report(out_dir)
        run = int(report_path.stem.removeprefix("run"))
        info = replace(info, run=run, links_sha256=sha, complete=not failed)
        write_report(report_path, build_report(result, violations, info))
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write the outputs: {exc}") from exc
    written = [report_path] if sha is None else [links_path, report_path]
    print(f"{len(result.links)} links ({len(result.exact)} exact-title), "
          f"{len(result.decisions)} pairs adjudicated; wrote "
          f"{', '.join(_display(p) for p in written)}")
    if failed:
        print(f"{failed} adjudicator calls failed; the link file was not rewritten. Rerun to try "
              "them again.", file=sys.stderr)
        return EXIT_RUN_ERROR
    if violations:
        print(f"{len(violations)} links fail check_batch(), such as {violations[0]}; the link "
              "file was not rewritten.", file=sys.stderr)
        return EXIT_CHECK_FAILED
    return EXIT_OK


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
