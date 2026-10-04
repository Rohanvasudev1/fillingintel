"""How an eval set is spread across classes, companies, periods and splits (Step 4).

Usage::

    uv run python -m eval.coverage eval/agent_drafted_set.jsonl
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from eval.schema import EvalRecord, load_records

EXIT_UNREADABLE = 2

# RUNBOOK Step 4 targets, plus the decline and unanswerable classes from the Step 4 plan.
TARGETS = {
    "lookup": 40,
    "local": 35,
    "multi_hop": 30,
    "global": 15,
    "decline": 10,
    "unanswerable": 10,
}


@dataclass(frozen=True)
class Coverage:
    total: int
    by_class: dict[str, int]
    by_ticker: dict[str, int]  # a record counts once per ticker it covers
    by_period: dict[str, int]
    by_split: dict[str, int]
    by_provenance: dict[str, int]
    by_difficulty: dict[str, int]


def coverage(records: list[EvalRecord]) -> Coverage:
    return Coverage(
        total=len(records),
        by_class=dict(Counter(r.class_ for r in records)),
        by_ticker=dict(Counter(t for r in records for t in r.tickers)),
        by_period=dict(Counter(p for r in records for p in r.fiscal_periods)),
        by_split=dict(Counter(r.split for r in records)),
        by_provenance=dict(Counter(r.provenance for r in records)),
        by_difficulty=dict(Counter(r.difficulty for r in records)),
    )


def _table(title: str, counts: dict[str, int]) -> list[str]:
    return [f"## {title}", "", "| value | count |", "|---|---|"] + [
        f"| {k} | {v} |" for k, v in sorted(counts.items())
    ] + [""]


def format_coverage(report: Coverage) -> str:
    lines = [
        f"# Eval-set coverage ({report.total} records)",
        "",
        "## By class",
        "",
        "| class | count | target |",
        "|---|---|---|",
    ]
    lines += [f"| {c} | {report.by_class.get(c, 0)} | {t} |" for c, t in TARGETS.items()]
    lines.append("")
    lines += _table("By ticker", report.by_ticker)
    lines += _table("By fiscal period", report.by_period)
    lines += _table("By split", report.by_split)
    lines += _table("By provenance", report.by_provenance)
    lines += _table("By difficulty", report.by_difficulty)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise an eval set's coverage.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        records = load_records(args.path)
    except (OSError, ValueError) as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(format_coverage(coverage(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
