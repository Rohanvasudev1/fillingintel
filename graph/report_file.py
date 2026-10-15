"""The reports run file, `benchmarks/communities/reports-run{N}.jsonl` (Step 12).

The first line is a header: the run, commit, the communities file and
community graph hash the reports were written for, the prompt, model and
settings, the estimate, totals, the failures and whether the run is complete.
Then one line per reportable community that got a report (`kind` "report"),
in the communities file's order, lower before top. `cost_this_run_usd` is
what this run's uncached calls cost; Step 12's spend sums it over the
committed files (graph.step12_spend).
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

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
    failed = sum(anthropic_cost(REPORT_MODEL, f.usage, REPORT_PRICES) for f in result.failures
                 if f.usage is not None and not f.from_cache)
    return reports + failed


def record_json(record: ReportRecord) -> dict[str, object]:
    report = record.report
    return {
        "kind": "report", "community_id": record.community_id,
        "member_hash": record.member_hash, "level": record.level, "status": record.status,
        "title": report.title if report else "", "summary": report.summary if report else "",
        "findings": list(report.findings) if report else [], "report": record.text,
        "report_chunk_ids": list(record.chunk_ids),
        "kept_sentences": report.kept_sentences if report else 0,
        "dropped": report.dropped if report else 0,
        "dropped_by_reason": dict(report.dropped_by_reason) if report else {},
        "inputs": list(record.inputs), "prompt_version": record.prompt_version,
        "model": REPORT_MODEL, "effort": REPORT_EFFORT, "usage": _usage(record.usage),
        "cost_usd": record.cost_usd, "from_cache": record.from_cache,
    }


def failure_json(failure: ReportFailure) -> dict[str, object]:
    return {"community_id": failure.community_id, "level": failure.level,
            "reason": failure.reason, "detail": failure.detail,
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
        "kept_sentences": sum(r.report.kept_sentences for r in records if r.report),
        "dropped_sentences": sum(r.report.dropped for r in records if r.report),
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
