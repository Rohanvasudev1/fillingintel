"""Resolve the extracted nodes of every candidates file and write the outputs (Step 9).

Usage (``VOYAGE_API_KEY`` is needed only when some name embeddings are not
cached yet)::

    uv run --env-file .env python -m resolve.run
    uv run --env-file .env python -m resolve.run --candidates data/extract/<no>-run1.jsonl

By default the command reads the latest run of each accession in
``data/extract/``. It embeds the named nodes through the batched Voyage path and
its disk cache (``data/cache/entity_embeddings/``), scores every same-label
pair, makes the deterministic merges, and leaves every other candidate pair
pending until ticket 03 adds the adjudicator. New merges are appended to
``benchmarks/resolution/merge_log.jsonl``; the alias map
(``alias_map.json``) is rewritten as the replay of the whole log; the run
report goes to ``run{N}.json``. A rerun on unchanged inputs appends nothing.

Exit codes: 0 done; 2 bad input (bad arguments, no candidates file, a
malformed candidates file or merge log); 3 could not run (``VOYAGE_API_KEY``
missing while embeddings are uncached, a Voyage, cache or file error);
4 checks failed (the alias map on disk is not the replay of the merge log on
disk, so one of them was edited by hand).
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ingest.provenance import REPO_ROOT, git_state
from ingest.voyage import BatchEmbedder, BatchEmbedding, VoyageClient, VoyageError
from resolve.candidates import is_named
from resolve.config import ResolveConfig
from resolve.embed import VoyageNodeEmbedder, embed_text
from resolve.log import LogError, Stamp, append_events, read_log, replay
from resolve.nodes import InputError, ResolveNode, load_nodes
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
from resolve.report import EmbeddingUse, RunInfo, build_report, write_report
from retrieve.query_cache import BATCH_CACHE_DIR, CachedBatchEmbedder, CacheError

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_CHECK_FAILED = 4

OpenClient = Callable[[], AbstractContextManager[BatchEmbedder]]
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


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m resolve.run", description=__doc__.split(
        "\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", type=Path, nargs="+",
                        help="candidates files (default: the latest run of each accession)")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


def _voyage_client() -> AbstractContextManager[BatchEmbedder]:
    return VoyageClient.from_env()


def main(argv: Sequence[str] | None = None, *, candidates_dir: Path = CANDIDATES_DIR,
         out_dir: Path = OUTPUT_DIR, embed_cache: Path = BATCH_CACHE_DIR,
         open_client: OpenClient = _voyage_client, config: ResolveConfig | None = None) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        args = _parse_args(argv)
        return _run(args.candidates or latest_candidates(candidates_dir), out_dir, embed_cache,
                    open_client, config or ResolveConfig())
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def _run(files: Sequence[Path], out_dir: Path, embed_cache: Path, open_client: OpenClient,
         config: ResolveConfig) -> int:
    if not files:
        raise _Stop(EXIT_USAGE, "no candidates files; run python -m extract.run first")
    log_path, map_path = out_dir / MERGE_LOG_NAME, out_dir / ALIAS_MAP_NAME
    try:
        nodes, inputs = load_nodes(files)
        log = read_log(log_path)
        on_disk = read_alias_map(map_path)
        replayed = replay(log)
    except (InputError, LogError, ValueError) as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the merge log or alias map: {exc}") from exc
    if on_disk != replayed:
        raise _Stop(EXIT_CHECK_FAILED, f"{map_path.name} is not the replay of {log_path.name}; "
                                       "one was edited by hand")
    stamp = Stamp(config.config_hash, git_state(), datetime.now(UTC).isoformat(timespec="seconds"))
    texts = [embed_text(n, config.embed_text) for n in nodes if is_named(n.label)]
    with _cached_embedder(texts, config, embed_cache, open_client) as cache:
        embedder = VoyageNodeEmbedder(cache, config.embedding_model, config.embed_input_type)
        try:
            result = resolve_nodes(nodes, embedder if config.top_k else None, None, config,
                                   log=log, stamp=stamp)
        except (VoyageError, CacheError, OSError) as exc:
            raise _Stop(EXIT_RUN_ERROR, f"could not embed the names: {exc}") from exc
        except ValueError as exc:  # a vector missing, empty or miscounted
            raise _Stop(EXIT_RUN_ERROR, f"could not resolve: {exc}") from exc
    try:
        append_events(log_path, result.merges)
        aliases = replay(read_log(log_path))
        write_alias_map(map_path, aliases)
        report_path = next_report(out_dir)
        info = RunInfo(run=int(report_path.stem.removeprefix("run")), stamp=stamp, config=config,
                       inputs=inputs, embedding=EmbeddingUse(len(set(texts)), cache.calls,
                                                             cache.tokens),
                       alias_map_keys=len(aliases))
        write_report(report_path, build_report(result, info))
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write the outputs: {exc}") from exc
    print(_summary(nodes, result, [log_path, map_path, report_path]))
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
            f"{len(result.merges)} new merges ({len(result.proposed)} in all), "
            f"{len(result.pending)} pending; wrote {shown}")


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
