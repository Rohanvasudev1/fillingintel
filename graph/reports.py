"""`python -m graph.reports`: write a cited report for every reportable community (Step 12).

Usage (needs ``ANTHROPIC_API_KEY`` and ``DATABASE_URL``, with
``ANTHROPIC_BASE_URL`` unset)::

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python -m graph.reports
    env -u ANTHROPIC_BASE_URL uv run --env-file .env python -m graph.reports --send

It reads the latest `benchmarks/communities/communities-run{N}.json` and
rebuilds what `graph.load` builds (Postgres, the extract files, the alias map
and the persistence links), refusing a communities file made from other
builder output. Each reportable lower community's report is written from its
members, member edges and most-cited chunk texts; each top one from its lower
reports (graph.report_input). claude-haiku-5-5 writes them under prompt
`report/v1`, 4 calls at a time, through the response cache, and the citation
check drops every sentence that does not cite the community's evidence.

Without ``--send`` it prints the estimate for the calls not cached and sends
nothing. With ``--send`` it refuses when the estimate is over ``--max-cost``
(default $1) or would take Step 12's spend, summed over the committed run
files, over its $6 cap. A run writes `benchmarks/communities/reports-run{N}.jsonl`.

Exit codes: 0 written, or the estimate printed; 2 bad input (bad arguments, no
communities file or a malformed one, or one made from other builder output);
3 could not run (a setting missing, ``ANTHROPIC_BASE_URL`` set, Postgres
unreachable, a chunk text missing, a refusal over a cap, a cache or file
error, or a report call failed: the file is then written with
``complete: false``, and a rerun replays what was cached and tries again).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from graph.batch import Batch
from graph.community import LOWER, TOP, Community
from graph.community_file import (
    CommunitiesFile,
    CommunitiesFileError,
    latest_communities_file,
    read_communities_file,
)
from graph.community_graph import DuplicateKey, community_graph
from graph.repo_build import DATABASE_ENV, CouldNotRun, read_built
from graph.report_estimate import estimate_reports
from graph.report_file import next_reports_path, run_header, write_reports_file
from graph.report_input import DEFAULT_BUDGET, CommunityIndex
from graph.report_prompt import PromptError, ReportPrompt, load_prompt
from graph.report_request import REPORT_MODEL
from graph.report_writer import (
    WORKERS,
    Outcome,
    Planned,
    ReportWriter,
    plan_lower,
    plan_top,
    reportable,
)
from graph.step12_spend import COMMUNITIES_DIR, STEP_12_CAP_USD, spent_usd
from ingest.provenance import REPO_ROOT, git_state
from ingest.store import filing_chunk_texts, list_filings
from resolve.cli import cost_cap
from resolve.estimate import CostEstimate
from retrieve.answer_model import (
    API_KEY_ENV,
    AnswerModel,
    AnswerModelError,
    AnthropicAnswerModel,
    ApiResponse,
    ModelRequest,
)
from retrieve.query_cache import CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
MAX_COST_USD = 1.0
BASE_URL_ENV = "ANTHROPIC_BASE_URL"

ReadInputs = Callable[[], tuple[Batch, Mapping[str, str]]]
OpenModel = Callable[[], AbstractContextManager[AnswerModel]]


class _Stop(Exception):
    """The run stops with *code*; the message is safe to print."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class _Unsent:
    """The inner model while estimating: a cache miss here is a bug, never a call."""

    def complete(self, request: ModelRequest) -> ApiResponse:
        raise RuntimeError("graph.reports tried to send a call without --send")


@dataclass(frozen=True, slots=True)
class _Plan:
    """What the estimate covers: the uncached requests and the top calls sized by guess."""

    lower: list[Planned]
    uncached: list[Planned]
    assumed_tops: list[int]
    estimate: CostEstimate


def read_batch_and_texts() -> tuple[Batch, Mapping[str, str]]:
    """The builder's batch, as `graph.load` builds it, and every chunk's text."""
    _, built = read_built()
    try:
        with psycopg.connect(os.environ[DATABASE_ENV]) as conn:
            conn.read_only = True
            texts = {c.chunk_id: c.text for f in list_filings(conn)
                     for c in filing_chunk_texts(conn, f.accession_no)}
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise CouldNotRun(f"could not read the chunk texts: database error "
                          f"({type(exc).__name__})") from exc
    return built.batch, texts


def _anthropic_model() -> AbstractContextManager[AnswerModel]:
    return AnthropicAnswerModel.from_env()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m graph.reports", description=__doc__.split(
        "\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true",
                        help="make the calls; without it only the estimate is printed")
    parser.add_argument("--max-cost", type=cost_cap, default=MAX_COST_USD,
                        help=f"refuse a run estimated above this many USD "
                             f"(default {MAX_COST_USD:g})")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


def _check_env() -> None:
    if not os.environ.get(API_KEY_ENV):
        raise _Stop(EXIT_RUN_ERROR, f"{API_KEY_ENV} not set (run with: uv run --env-file .env "
                                    "python -m graph.reports ...)")
    if os.environ.get(BASE_URL_ENV):
        raise _Stop(EXIT_RUN_ERROR, f"{BASE_URL_ENV} is set; unset it so filing text and the "
                                    "API key go only to Anthropic")


def main(argv: Sequence[str] | None = None, *, communities_dir: Path = COMMUNITIES_DIR,
         read: ReadInputs = read_batch_and_texts, open_model: OpenModel = _anthropic_model,
         response_cache: Path = DEFAULT_CACHE_DIR) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    try:
        args = _parse_args(argv)
        _check_env()
        prompt = _prompt()
        communities = _communities(communities_dir)
        batch, texts = _inputs(read, communities)
        index = CommunityIndex.from_batch(batch)
        model = open_model if args.send else _unsent
        with _opened(model) as inner:
            writer = ReportWriter(CachedAnswerModel(inner, response_cache), prompt)
            plan = _plan(writer, communities.communities, index, texts, prompt, response_cache)
            spent = _spent(communities_dir)
            _print_estimate(plan, spent, args.max_cost)
            if not args.send:
                print("Estimate only; nothing was sent. Rerun with --send to write the reports.")
                return EXIT_OK
            _check_caps(plan.estimate, spent, args.max_cost)
            return _run(writer, communities, index, texts, prompt, plan.estimate,
                        communities_dir)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def _prompt() -> ReportPrompt:
    try:
        return load_prompt()
    except PromptError as exc:
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc


def _communities(directory: Path) -> CommunitiesFile:
    try:
        return read_communities_file(latest_communities_file(directory))
    except CommunitiesFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc


def _inputs(read: ReadInputs, communities: CommunitiesFile) -> tuple[Batch, Mapping[str, str]]:
    try:
        batch, texts = read()
        input_sha = community_graph(batch).input_sha256
    except (CouldNotRun, DuplicateKey) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not read the inputs: {exc}") from exc
    if input_sha != communities.input_sha256:
        raise _Stop(EXIT_USAGE, f"{communities.name} was made from other builder output; rerun "
                                "python -m graph.communities first")
    return batch, texts


@contextmanager
def _unsent() -> Iterator[AnswerModel]:
    yield _Unsent()


@contextmanager
def _opened(open_model: OpenModel) -> Iterator[AnswerModel]:
    try:
        client = open_model()
    except AnswerModelError as exc:  # MissingApiKey
        raise _Stop(EXIT_RUN_ERROR, f"cannot open the Anthropic client: {exc}") from exc
    with client as model:
        yield model


def _plan(writer: ReportWriter, communities: Sequence[Community], index: CommunityIndex,
          texts: Mapping[str, str], prompt: ReportPrompt, cache_dir: Path) -> _Plan:
    """The estimate. Top requests are built exactly when every lower reply is cached."""
    cache = CachedAnswerModel(_Unsent(), cache_dir)
    try:
        lower = plan_lower(communities, index, texts, prompt)
    except ValueError as exc:  # a chunk text is missing
        raise _Stop(EXIT_RUN_ERROR, f"cannot build the report requests: {exc}") from exc
    uncached = [p for p in lower if not cache.contains(p.request)]
    assumed: list[int] = []
    if uncached:
        assumed = [sum(c.parent == top.community_id for c in reportable(communities, LOWER))
                   for top in reportable(communities, TOP)]
    else:
        tops = plan_top(communities, _replay(writer, lower), prompt)
        uncached = [p for p in tops if isinstance(p, Planned) and not cache.contains(p.request)]
    estimate = estimate_reports([p.request for p in uncached], assumed, prompt, WORKERS)
    return _Plan(lower, uncached, assumed, estimate)


def _replay(writer: ReportWriter, lower: list[Planned]) -> list[Outcome]:
    try:
        return writer.write_planned(lower)
    except CacheError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"response cache error: {exc}") from exc


def _spent(directory: Path) -> float:
    try:
        return spent_usd(directory)
    except ValueError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot sum Step 12's spend: {exc}") from exc


def _print_estimate(plan: _Plan, spent: float, max_cost: float) -> None:
    sized = f", {len(plan.assumed_tops)} top calls sized by guess" if plan.assumed_tops else ""
    estimate = plan.estimate
    print(f"{len(plan.lower)} lower reports; {estimate.calls} calls not cached{sized}; "
          f"estimate ${estimate.usd:.2f} ({REPORT_MODEL}, {estimate.price_table_date} prices; "
          f"--max-cost ${max_cost:.2f}). Step 12 spend so far ${spent:.2f} of "
          f"${STEP_12_CAP_USD:.2f}.")


def _check_caps(estimate: CostEstimate, spent: float, max_cost: float) -> None:
    if estimate.usd > max_cost:
        raise _Stop(EXIT_RUN_ERROR, f"the estimate ${estimate.usd:.2f} is over --max-cost "
                                    f"${max_cost:.2f}; no report call was made")
    if spent + estimate.usd > STEP_12_CAP_USD:
        raise _Stop(EXIT_RUN_ERROR, f"Step 12 has spent ${spent:.2f}; the estimate "
                                    f"${estimate.usd:.2f} would pass the ${STEP_12_CAP_USD:.2f} "
                                    "cap; no report call was made")


def _run(writer: ReportWriter, communities: CommunitiesFile, index: CommunityIndex,
         texts: Mapping[str, str], prompt: ReportPrompt, estimate: CostEstimate,
         out_dir: Path) -> int:
    commit, created_at = git_state(), datetime.now(UTC).isoformat(timespec="seconds")
    try:
        result = writer.write_all(communities.communities, index, texts, DEFAULT_BUDGET)
        path = next_reports_path(out_dir)
        run = int(path.stem.removeprefix("reports-run"))
        header = run_header(run, commit, created_at,
                            {"file": communities.name, "sha256": communities.sha256,
                             "community_graph_input_sha256": communities.input_sha256},
                            prompt, DEFAULT_BUDGET, WORKERS, estimate, result)
        write_reports_file(path, header, result)
    except (CacheError, OSError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write the reports: {exc}") from exc
    records = result.records
    dropped = sum(r.report.dropped for r in records)
    print(f"{len(records)} reports ({dropped} sentences dropped); this run cost "
          f"${header['cost_this_run_usd']:.4f}. Wrote {_display(path)}")
    if result.failures:
        print(f"{len(result.failures)} communities have no report, such as "
              f"{result.failures[0].community_id} ({result.failures[0].reason}); the file is "
              "marked incomplete. Rerun to try them again.", file=sys.stderr)
        return EXIT_RUN_ERROR
    return EXIT_OK


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    sys.exit(main())
