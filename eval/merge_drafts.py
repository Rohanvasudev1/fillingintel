"""Merge subagent draft slices into ``eval/agent_drafted_set.jsonl`` (Step 4).

Usage::

    uv run python -m eval.merge_drafts

Reads ``eval/drafts/*.jsonl`` in file-name order, drops questions already seen
(case and spacing ignored), renumbers q0001 onwards, recomputes each split
from the question text, and validates the result as a drafted set.  Nothing
is written unless validation finds no problems.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval.corpus_index import ChunkIndex
from eval.coverage import coverage, format_coverage
from eval.schema import EvalRecord, dump_records, load_records, normalise_question, split_for
from eval.validate import validate
from ingest.parsed_files import DEFAULT_PARSED_DIR
from ingest.provenance import REPO_ROOT

DEFAULT_DRAFTS_DIR = REPO_ROOT / "eval" / "drafts"
DEFAULT_OUTPUT = REPO_ROOT / "eval" / "agent_drafted_set.jsonl"
EXIT_UNREADABLE = 2


def merge(
    drafts_dir: Path, index: ChunkIndex
) -> tuple[list[EvalRecord], list[str], list[str]]:
    """``(records, dropped, problems)``: merged records, ``file:id`` of repeats, problems."""
    paths = sorted(drafts_dir.glob("*.jsonl"))
    if not paths:
        raise FileNotFoundError(f"no draft slices in {drafts_dir}")
    merged: list[EvalRecord] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for draft in load_records(path):
            key = normalise_question(draft.question)
            if key in seen:
                dropped.append(f"{path.name}:{draft.id}")
                continue
            seen.add(key)
            merged.append(
                draft.model_copy(
                    update={"id": f"q{len(merged) + 1:04d}", "split": split_for(draft.question)}
                )
            )
    return merged, dropped, validate(merged, index, human_set=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge agent draft slices into one set.")
    parser.add_argument("--drafts-dir", type=Path, default=DEFAULT_DRAFTS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    args = parser.parse_args(argv)
    try:
        index = ChunkIndex.from_parsed_dir(args.parsed_dir)
        records, dropped, problems = merge(args.drafts_dir, index)
    except (OSError, ValueError) as exc:
        print(f"cannot merge: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    for item in dropped:
        print(f"dropped repeat {item}")
    for line in problems:
        print(line)
    if problems:
        print(f"{len(problems)} problems; nothing written")
        return 1
    dump_records(records, args.output)
    print(format_coverage(coverage(records)))
    print(f"wrote {len(records)} records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
