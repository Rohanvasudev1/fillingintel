"""Find chunks for an eval question, and print a chunk's exact text (Step 4).

Usage::

    uv run python -m eval.search foundry capacity --ticker INTC [--period FY2025]
    uv run python -m eval.search --show 0000050863-26-000011:0042

Results come from ``data/parsed`` through the same chunker as the database,
so a chunk ID found here is the ID ``resolve()`` returns text for.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval.corpus_index import ChunkIndex
from ingest.parsed_files import DEFAULT_PARSED_DIR, text_sha256

DEFAULT_LIMIT = 10
EXIT_UNREADABLE = 2


def _show(index: ChunkIndex, chunk_id: str) -> int:
    info, text = index.info(chunk_id), index.text(chunk_id)
    if info is None or text is None:
        print(f"unknown chunk {chunk_id}", file=sys.stderr)
        return 1
    print(f"{chunk_id}  {info.ticker} {info.form_type} {info.fiscal_period} {info.section}")
    print(f"sha256 {text_sha256(text)}")
    print()
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search corpus chunks or show one.")
    parser.add_argument("terms", nargs="*", help="words that must all appear")
    parser.add_argument("--show", metavar="CHUNK_ID")
    parser.add_argument("--ticker")
    parser.add_argument("--period", help="fiscal period, e.g. FY2025 or FY2025-Q2")
    parser.add_argument("--section", help="section label, e.g. part_i_item_1a")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    args = parser.parse_args(argv)
    try:
        index = ChunkIndex.from_parsed_dir(args.parsed_dir)
    except OSError as exc:
        print(f"cannot read the corpus: {exc} (run python -m ingest.corpus)", file=sys.stderr)
        return EXIT_UNREADABLE
    if args.show:
        return _show(index, args.show)
    if not args.terms:
        parser.error("give search terms or --show CHUNK_ID")
    hits = index.search(" ".join(args.terms), args.ticker, args.period, args.section, args.limit)
    for hit in hits:
        info = index.info(hit.chunk_id)
        if info is None:  # cannot happen: hits come from the index
            continue
        print(
            f"{hit.chunk_id}  {info.ticker} {info.fiscal_period} {info.section}  "
            f"[{hit.score}]  {hit.snippet}"
        )
    if not hits:
        print("no chunks contain all of those words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
