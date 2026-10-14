"""Embed the graph's node texts for graph_local: ``python -m retrieve.node_embed``.

Reads every extracted node from ``NEO4J_*``, builds its text ("Label: name",
"RiskFactor: title", "MetricValue: concept, period"), and prints how many texts
the entity-embedding cache lacks and what embedding them would cost.  Nothing
is sent unless ``--embed`` is given, and then only when the estimate is within
``--max-cost``.  Texts go to Voyage with input type ``document``, in batches,
and each vector is cached on disk.

The estimate counts tokens with cl100k_base and scales them by
``VOYAGE_TOKEN_FACTOR``, since Voyage's tokenizer is not available offline.

Exit codes: 0 done, 2 bad arguments, 3 could not run.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from neo4j.exceptions import DriverError, Neo4jError

from graph.connection import GraphSettingsMissing, driver_from_env
from ingest.chunker import count_tokens
from ingest.voyage import DEFAULT_MODEL, BatchEmbedder, VoyageClient, VoyageError
from retrieve.anchors import AnchorNode
from retrieve.graph_nodes import NODE_INPUT_TYPE, distinct_texts, read_anchor_nodes
from retrieve.pricing import embedding_cost
from retrieve.query_cache import BATCH_CACHE_DIR, CachedBatchEmbedder, CacheError, CacheOnly

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_COULD_NOT_RUN = 3
VOYAGE_TOKEN_FACTOR = 1.3  # cl100k_base tokens to an upper guess at Voyage's
DEFAULT_MAX_COST_USD = 0.50

ReadNodes = Callable[[], Sequence[AnchorNode]]
OpenVoyage = Callable[[], AbstractContextManager[BatchEmbedder]]


def _read_graph_nodes() -> Sequence[AnchorNode]:
    with driver_from_env() as driver:
        return read_anchor_nodes(driver)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed graph node texts for graph_local; prints the estimate first.")
    parser.add_argument("--embed", action="store_true",
                        help="send the texts not yet cached to Voyage")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
                        help=f"refuse when the estimate is above this, in USD "
                             f"(default {DEFAULT_MAX_COST_USD})")
    return parser.parse_args(argv)


def estimate_usd(texts: Sequence[str], model: str = DEFAULT_MODEL) -> tuple[int, float]:
    """Estimated Voyage tokens and USD for embedding *texts*."""
    tokens = math.ceil(sum(count_tokens(t) for t in texts) * VOYAGE_TOKEN_FACTOR)
    return tokens, embedding_cost(model, tokens)


def main(argv: Sequence[str] | None = None, *, read_nodes: ReadNodes = _read_graph_nodes,
         open_voyage: OpenVoyage = VoyageClient.from_env,
         cache_dir: Path = BATCH_CACHE_DIR) -> int:
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    try:
        nodes = read_nodes()
    except (GraphSettingsMissing, Neo4jError, DriverError, ValueError) as exc:
        print(f"could not read the graph's nodes ({type(exc).__name__}): {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    texts = distinct_texts(nodes)
    lister = CachedBatchEmbedder(CacheOnly("listing never embeds"), cache_dir=cache_dir)
    missing = lister.uncached(texts, DEFAULT_MODEL, NODE_INPUT_TYPE)
    tokens, usd = estimate_usd(missing)
    print(f"{len(nodes)} nodes, {len(texts)} distinct texts, {len(missing)} not cached.")
    print(f"Embedding them: estimate about {tokens:,} tokens, ${usd:.4f} with {DEFAULT_MODEL}.")
    if not args.embed:
        print("Nothing sent. Run again with --embed to embed them.")
        return EXIT_OK
    if usd > args.max_cost:
        print(f"The estimate ${usd:.4f} is above --max-cost ${args.max_cost:.2f}; nothing sent.",
              file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    return _embed(missing, open_voyage, cache_dir)


def _embed(texts: Sequence[str], open_voyage: OpenVoyage, cache_dir: Path) -> int:
    if not texts:
        return EXIT_OK
    try:
        with open_voyage() as voyage:
            cache = CachedBatchEmbedder(voyage, cache_dir=cache_dir)
            cache.embed(texts, DEFAULT_MODEL, NODE_INPUT_TYPE)
    except (VoyageError, CacheError, OSError) as exc:
        print(f"embedding failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return EXIT_COULD_NOT_RUN
    print(f"Embedded {len(texts)} texts in {cache.calls} requests; Voyage reported "
          f"{cache.tokens:,} tokens, ${embedding_cost(DEFAULT_MODEL, cache.tokens):.4f}.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
