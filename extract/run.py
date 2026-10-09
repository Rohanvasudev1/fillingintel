"""Extract one 10-K by accession number and write its run report (Step 8).

Usage (needs ``ANTHROPIC_API_KEY`` and ``DATABASE_URL``, with
``ANTHROPIC_BASE_URL`` unset; chunks not yet in the response cache need the
network)::

    uv run --env-file .env python -m extract.run --accession 0001045810-26-000021
    uv run --env-file .env python -m extract.run --accession <no> --max-cost 5

The command reads the filing and its chunk texts (the text ``resolve()``
returns) from Postgres, prints a cost estimate for the calls not already in the
response cache and stops if it is over ``--max-cost`` (default $10). It then
runs ``extract_filing()`` 4 calls at a time and writes the candidates file
(``data/extract/``, gitignored) and the run report (``benchmarks/extraction/``,
committed). A rerun with the same prompt replays every reply from the cache.

With ``PHOENIX_COLLECTOR_ENDPOINT`` set, the run is one trace: a root span and
one LLM span per chunk call (``extract.tracing``). Unset, nothing is traced.

Exit codes: 0 every chunk extracted and ``check_batch()`` found nothing;
2 bad arguments; 3 could not run (a setting missing, Postgres unreachable, an
unknown accession, an estimate over the cap, a cache or file error); 4 the run
finished incomplete (a failed chunk or a ``check_batch()`` violation).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import psycopg

from extract.estimate import EXTRACTION_PRICES, CostEstimate, estimate_cost
from extract.filers import FilingInfo
from extract.pipeline import ExtractionRun, extract_filing
from extract.prompt import ExtractPrompt, PromptError, load_prompt
from extract.report import (
    CANDIDATES_DIR,
    REPORTS_DIR,
    RunInfo,
    build_report,
    candidate_lines,
    candidates_path,
    next_run_number,
    report_path,
    write_candidates,
    write_report,
)
from extract.request import ChunkInput, ExtractRequest, build_request
from extract.tracing import RUN_SPAN, TracedExtractModel, run_attributes
from ingest.provenance import REPO_ROOT, git_state
from ingest.store import filing_chunk_texts, get_filing_summary
from retrieve.answer_model import API_KEY_ENV, AnswerModel, AnthropicAnswerModel
from retrieve.query_cache import CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel
from retrieve.tracing import (
    Kind,
    SpanRecorder,
    TracingConfigError,
    build_provider,
    text_capture,
)

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_INCOMPLETE = 4
WORKERS = 4
DEFAULT_MAX_COST_USD = 10.0
DATABASE_ENV = "DATABASE_URL"
BASE_URL_ENV = "ANTHROPIC_BASE_URL"
TRACER_NAME = "filingintel.extract"
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")

OpenModel = Callable[[], AbstractContextManager[AnswerModel]]
Connect = Callable[[str], AbstractContextManager[psycopg.Connection]]
class Extract(Protocol):
    """``extract_filing()``'s signature, so tests can wrap it."""

    def __call__(self, filing: FilingInfo, chunks: Sequence[ChunkInput], model: AnswerModel,
                 prompt: ExtractPrompt, workers: int = 1) -> ExtractionRun: ...

logger = logging.getLogger(__name__)


class _Stop(Exception):
    """The run stops with *code*; the message is safe to print."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _accession(value: str) -> str:
    if not _ACCESSION.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an accession number like "
                                         "0001045810-26-000021")
    return value


def _cost_cap(value: str) -> float:
    try:
        cap = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not cap >= 0:  # also refuses nan
        raise argparse.ArgumentTypeError("--max-cost must be 0 or more")
    return cap


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract candidate graph records from a 10-K.")
    parser.add_argument("--accession", required=True, type=_accession,
                        help="the filing's accession number")
    parser.add_argument("--max-cost", type=_cost_cap, default=DEFAULT_MAX_COST_USD,
                        help=f"refuse a run estimated above this many USD "
                             f"(default {DEFAULT_MAX_COST_USD:g})")
    return parser.parse_args(argv)


def _check_env() -> str:
    """The database URL; stops when a setting is missing or ``ANTHROPIC_BASE_URL`` is set."""
    missing = [name for name in (API_KEY_ENV, DATABASE_ENV) if not os.environ.get(name)]
    if missing:
        raise _Stop(EXIT_RUN_ERROR, f"{', '.join(missing)} not set (run with: uv run "
                                    "--env-file .env python -m extract.run ...)")
    if os.environ.get(BASE_URL_ENV):
        raise _Stop(EXIT_RUN_ERROR, f"{BASE_URL_ENV} is set; unset it so filing text and the "
                                    "API key go only to Anthropic")
    return os.environ[DATABASE_ENV]


def _read_filing(connect: Connect, url: str,
                 accession_no: str) -> tuple[FilingInfo, list[ChunkInput]]:
    try:
        with connect(url) as conn:
            summary = get_filing_summary(conn, accession_no)
            texts = filing_chunk_texts(conn, accession_no) if summary is not None else []
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise _Stop(EXIT_RUN_ERROR, f"could not read the filing: database error "
                                    f"({type(exc).__name__})") from exc
    if summary is None or not texts:
        raise _Stop(EXIT_RUN_ERROR, f"{accession_no} has no chunks in Postgres; load it with "
                                    "python -m ingest.load")
    try:
        filing = FilingInfo(cik=summary.cik, accession_no=summary.accession_no,
                            company_name=summary.company_name, form_type=summary.form_type,
                            fiscal_period=summary.fiscal_period)
    except ValueError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot extract {accession_no}: {exc}") from exc
    return filing, [ChunkInput(t.chunk_id, t.section, t.text) for t in texts]


def _spans(tracing: Callable[[Mapping[str, str]], TracerProvider | None]
           ) -> tuple[SpanRecorder, TracerProvider | None]:
    try:
        capture = text_capture(os.environ)
        provider = tracing(os.environ)
    except TracingConfigError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    if provider is None:
        return SpanRecorder.off(), None
    return SpanRecorder(provider.get_tracer(TRACER_NAME), capture), provider


def _anthropic_model() -> AbstractContextManager[AnswerModel]:
    return AnthropicAnswerModel.from_env()


def main(
    argv: Sequence[str] | None = None,
    *,
    open_model: OpenModel = _anthropic_model,
    connect: Connect = psycopg.connect,
    candidates_dir: Path = CANDIDATES_DIR,
    reports_dir: Path = REPORTS_DIR,
    response_cache: Path = DEFAULT_CACHE_DIR,
    tracing: Callable[[Mapping[str, str]], TracerProvider | None] = build_provider,
    extract: Extract = extract_filing,
) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    provider = None
    try:
        url = _check_env()
        spans, provider = _spans(tracing)
        try:
            prompt = load_prompt()
        except PromptError as exc:
            raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
        filing, chunks = _read_filing(connect, url, args.accession)
        return _run(args, filing, chunks, prompt, spans, open_model=open_model,
                    candidates_dir=candidates_dir, reports_dir=reports_dir,
                    response_cache=response_cache, extract=extract)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code
    finally:
        if provider is not None:
            provider.shutdown()  # flushes queued spans


def _run(args: argparse.Namespace, filing: FilingInfo, chunks: Sequence[ChunkInput],
         prompt: ExtractPrompt, spans: SpanRecorder, *, open_model: OpenModel,
         candidates_dir: Path, reports_dir: Path, response_cache: Path,
         extract: Extract) -> int:
    commit = git_state()
    requests = [build_request(prompt, filing, chunk) for chunk in chunks]
    try:
        EXTRACTION_PRICES.price(requests[0].model)  # an unpriced model stops before any call
        with open_model() as inner:
            cached = CachedAnswerModel(inner, response_cache)
            estimate = _check_estimate([r for r in requests if not cached.contains(r)],
                                       filing, len(chunks), args.max_cost)
            model = TracedExtractModel(cached, spans, prompt.prompt_version, EXTRACTION_PRICES)
            with spans.span(RUN_SPAN, Kind.CHAIN, run_attributes(filing.accession_no)):
                run = extract(filing, chunks, model, prompt, workers=WORKERS)
        number = next_run_number(filing.accession_no, candidates_dir, reports_dir)
    except (CacheError, OSError, ValueError) as exc:  # OSError: a cache write or a folder read
        raise _Stop(EXIT_RUN_ERROR, f"run stopped, nothing written: {exc}") from exc
    candidates = candidates_path(candidates_dir, filing.accession_no, number)
    first = requests[0]
    info = RunInfo(
        run=number, commit=commit, created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        model=first.model, effort=first.effort, max_tokens=first.max_tokens, prompt=prompt,
        workers=WORKERS, estimate=estimate, prices=EXTRACTION_PRICES,
        candidates_file=_display(candidates),
    )
    return _write(run, info, candidates, report_path(reports_dir, filing.accession_no, number))


def _check_estimate(uncached: Sequence[ExtractRequest], filing: FilingInfo, chunk_count: int,
                    max_cost: float) -> CostEstimate:
    """The estimate for the *uncached* requests, printed; stops when it is over *max_cost*."""
    estimate = estimate_cost(uncached, WORKERS, EXTRACTION_PRICES)
    print(f"{filing.accession_no}: {chunk_count} chunks, {estimate.calls} not cached; "
          f"estimate ${estimate.usd:.2f} (cap ${max_cost:.2f}, "
          f"{estimate.price_table_date} prices)")
    if estimate.usd > max_cost:
        raise _Stop(EXIT_RUN_ERROR, f"the estimate ${estimate.usd:.2f} is over "
                                    f"--max-cost ${max_cost:.2f}; no call was made")
    return estimate


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _write(run: ExtractionRun, info: RunInfo, candidates: Path, report_file: Path) -> int:
    report = build_report(run, info)
    try:
        write_candidates(candidates, candidate_lines(run))
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write the candidates file: {exc}") from exc
    try:
        write_report(report_file, report)
    except OSError as exc:
        candidates.unlink(missing_ok=True)  # a run without its report does not count
        raise _Stop(EXIT_RUN_ERROR, f"could not write the run files: {exc}") from exc
    print(f"run {info.run}: {report['nodes']} nodes, {report['edges']} edges, "
          f"{report['rejections']['total']} rejections, {len(run.failures)} failed chunks, "
          f"{len(run.violations)} batch violations; cost ${report['cost_this_run_usd']:.2f} "
          f"this run")
    print(f"wrote {_display(candidates)} and {_display(report_file)}")
    if not run.complete:
        print(f"run {info.run} is INCOMPLETE: failed chunks or check_batch() violations",
              file=sys.stderr)
        return EXIT_INCOMPLETE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
