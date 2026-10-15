"""The reports run file, `benchmarks/communities/reports-run{N}.jsonl` (Step 12).

The first line is a header: the run, commit, the communities file and
community graph hash the reports were written for, the prompt, model and
settings, the estimate, totals, the failures and whether the run is complete.
Then one line per reportable community that got a report (`kind` "report"),
in the communities file's order, lower before top. `cost_this_run_usd` is
what this run's uncached calls cost; Step 12's spend sums it over the
committed files (graph.step12_spend). read_reports_file() reads a file back
for the graph load, checking each line at this boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from graph.community import LOWER, TOP, Level
from graph.report_input import InputBudget
from graph.report_prompt import ReportPrompt
from graph.report_request import (
    REPORT_EFFORT,
    REPORT_MAX_TOKENS,
    REPORT_MODEL,
    REPORT_PRICES,
    REPORT_PROVIDER,
)
from graph.report_writer import ReportFailure, ReportRecord, ReportsResult
from ingest.atomic_json import write_text_atomic
from resolve.estimate import CostEstimate
from retrieve.answer_model import TokenUsage
from retrieve.pricing import anthropic_cost

RUN_PREFIX = "reports-run"
_RUN = re.compile(rf"{RUN_PREFIX}(\d+)\.jsonl")


def next_reports_path(directory: Path) -> Path:
    used = [int(m[1]) for p in directory.glob(f"{RUN_PREFIX}*.jsonl")
            if (m := _RUN.fullmatch(p.name))]
    return directory / f"{RUN_PREFIX}{max(used, default=0) + 1}.jsonl"


def reports_runs(directory: Path) -> list[Path]:
    """Every reports run file in *directory*, by run number."""
    runs = [(int(m[1]), p) for p in directory.glob(f"{RUN_PREFIX}*.jsonl")
            if (m := _RUN.fullmatch(p.name))]
    return [p for _, p in sorted(runs)]


def _usage(usage: TokenUsage | None) -> dict[str, int] | None:
    return asdict(usage) if usage is not None else None


def _uncached_cost(result: ReportsResult) -> float:
    """What this run's calls cost, failed replies included; replays cost nothing."""
    reports = sum(r.cost_usd for r in result.records if r.usage is not None and not r.from_cache)
    failed = sum(anthropic_cost(f.model, f.usage, REPORT_PRICES) for f in result.failures
                 if f.usage is not None and not f.from_cache)
    return reports + failed


def record_json(record: ReportRecord) -> dict[str, object]:
    report = record.report
    return {
        "kind": "report", "community_id": record.community_id,
        "member_hash": record.member_hash, "level": record.level, "status": record.status,
        "title": report.title, "summary": report.summary, "findings": list(report.findings),
        "report": report.text, "report_chunk_ids": list(report.report_chunk_ids),
        "kept_sentences": report.kept_sentences, "dropped": report.dropped,
        "dropped_by_reason": dict(report.dropped_by_reason),
        "inputs": list(record.inputs), "prompt_version": record.prompt_version,
        "model": record.model, "effort": record.effort, "usage": _usage(record.usage),
        "cost_usd": record.cost_usd, "from_cache": record.from_cache,
    }


def failure_json(failure: ReportFailure) -> dict[str, object]:
    return {"community_id": failure.community_id, "level": failure.level,
            "reason": failure.reason, "detail": failure.detail, "model": failure.model,
            "usage": _usage(failure.usage), "from_cache": failure.from_cache}


def _totals(result: ReportsResult) -> dict[str, object]:
    records = result.records
    called = [r for r in records if r.usage is not None]
    usages = [r.usage for r in called if r.usage is not None]
    return {
        "reports": len(records),
        "by_level": {lvl: sum(r.level == lvl for r in records) for lvl in ("lower", "top")},
        "no_input": sum(r.status == "no_input" for r in records),
        "empty_reports": sum(r.status == "written" and not r.text for r in records),
        "calls": len(called), "from_cache": sum(r.from_cache for r in called),
        "input_tokens": sum(u.input_tokens for u in usages),
        "output_tokens": sum(u.output_tokens for u in usages),
        "cache_read_input_tokens": sum(u.cache_read_input_tokens for u in usages),
        "cache_creation_input_tokens": sum(u.cache_creation_input_tokens for u in usages),
        "cost_usd": sum(r.cost_usd for r in records),
        "kept_sentences": sum(r.report.kept_sentences for r in records),
        "dropped_sentences": sum(r.report.dropped for r in records),
        "failures": len(result.failures),
    }


def run_header(run: int, commit: str, created_at: str, communities: Mapping[str, str],
               prompt: ReportPrompt, budget: InputBudget, workers: int,
               estimate: CostEstimate, result: ReportsResult) -> dict[str, object]:
    return {
        "kind": "header", "run": run, "commit": commit, "created_at": created_at,
        "communities": dict(communities),
        "prompt": {"version": prompt.prompt_version, "path": prompt.path,
                   "file_sha256": prompt.file_sha256, "sha256": prompt.sha256},
        "provider": REPORT_PROVIDER, "model": REPORT_MODEL, "effort": REPORT_EFFORT,
        "max_tokens": REPORT_MAX_TOKENS, "price_table": REPORT_PRICES.date,
        "workers": workers, "input_budget": asdict(budget),
        "estimate": asdict(estimate),
        "totals": _totals(result),
        "cost_this_run_usd": _uncached_cost(result),
        "failures": [failure_json(f) for f in result.failures],
        "complete": not result.failures,
    }


def write_reports_file(path: Path, header: Mapping[str, object],
                       result: ReportsResult) -> None:
    lines = [header, *(record_json(r) for r in result.records)]
    write_text_atomic(path, "".join(json.dumps(line, ensure_ascii=False) + "\n"
                                    for line in lines))


# ── reading a file back (the graph load) ──────────────────────────────────────

class ReportsFileError(ValueError):
    """The reports file is missing or malformed; the message is safe to print."""


@dataclass(frozen=True, slots=True)
class ReportEntry:
    """One community's kept report as the graph stores it."""

    community_id: str
    member_hash: str
    level: Level
    title: str
    report: str
    report_chunk_ids: tuple[str, ...]
    prompt_version: str


@dataclass(frozen=True, slots=True)
class ReportsFile:
    name: str  # such as reports-run2.jsonl
    sha256: str
    communities_file: str  # the communities file the reports were written for
    communities_sha256: str
    community_graph_input_sha256: str
    complete: bool
    entries: Mapping[str, ReportEntry]  # by community_id


def latest_reports_file(directory: Path) -> Path:
    """The highest-numbered reports run file in *directory*."""
    runs = reports_runs(directory)
    if not runs:
        raise ReportsFileError(f"no {RUN_PREFIX}N.jsonl in {directory.name}; run "
                               "python -m graph.reports first")
    return runs[-1]


def read_reports_file(path: Path) -> ReportsFile:
    """The header facts and every report line of *path*; ReportsFileError on any bad line."""
    try:
        raw = path.read_bytes()
        lines = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        header = lines[0]
        if header["kind"] != "header":
            raise ValueError("the first line is not the header")
        communities = header["communities"]
        entries: dict[str, ReportEntry] = {}
        for number, line in enumerate(lines[1:], start=2):
            entry = _entry(line, number)
            if entry.community_id in entries:
                raise ValueError(f"line {number}: {entry.community_id} has two reports")
            entries[entry.community_id] = entry
        found = ReportsFile(path.name, hashlib.sha256(raw).hexdigest(),
                            _text(communities["file"], "communities.file"),
                            _text(communities["sha256"], "communities.sha256"),
                            _text(communities["community_graph_input_sha256"],
                                  "communities.community_graph_input_sha256"),
                            header["complete"], MappingProxyType(entries))
        if not isinstance(found.complete, bool):
            raise ValueError("complete is not a boolean")
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise ReportsFileError(f"cannot read {path.name}: {type(exc).__name__}: {exc}") \
            from exc
    return found


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} is not a string")
    return value


def _entry(line: dict[str, object], number: int) -> ReportEntry:
    if line["kind"] != "report":
        raise ValueError(f"line {number} is not a report")
    level, chunk_ids = line["level"], line["report_chunk_ids"]
    if level not in (TOP, LOWER):
        raise ValueError(f"line {number}: level {level!r} is not top or lower")
    if not isinstance(chunk_ids, list) or not all(isinstance(c, str) for c in chunk_ids):
        raise ValueError(f"line {number}: report_chunk_ids is not a list of chunk IDs")
    texts = {name: _text(line[name], f"line {number}: {name}")
             for name in ("community_id", "member_hash", "title", "report", "prompt_version")}
    return ReportEntry(texts["community_id"], texts["member_hash"], level, texts["title"],
                       texts["report"], tuple(chunk_ids), texts["prompt_version"])
