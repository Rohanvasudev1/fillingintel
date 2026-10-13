"""Draw the persistence scoring pairs and have gpt-6-luna label them (Step 10, ticket 06).

Usage (needs ``OPENAI_API_KEY`` and ``DATABASE_URL``, with ``OPENAI_BASE_URL``
unset; run ``python -m persist.freeze`` first)::

    uv run --env-file .env python -m persist.label
    uv run --env-file .env python -m persist.label --max-cost 0.5

The command checks that the code, prompts, models and link file still match
``frozen.json``, reads the risk factors from the frozen run's own input files
(by name and SHA-256) and the filing list from Postgres, and rebuilds the
population (persist.sample) with every vector read from the embedding cache
only. The candidate stratum must be exactly the frozen run's exact-title links
and adjudicated pairs. The draw goes to ``scoring-draw.json`` once; a later
run must draw the same pairs.

It then reads the shown chunks from Postgres, prints a cost estimate for the
calls not in the response cache, stops if it is over ``--max-cost`` (default
$1), and labels the pairs 4 at a time. Each label is appended to
``scoring-labels.jsonl`` as it arrives, and a pair already labelled is never
sent again. The run's counts, tokens and cost go to
``scoring-label-run{N}.json``. Every figure built on these labels is
"agent-labelled (gpt-6-luna)".

Exit codes: 0 every drawn pair is labelled; 2 bad arguments or a malformed
file; 3 could not run (a setting missing, Postgres unreachable, an uncached
embedding, the estimate over the cap, an API, cache or file error); 4 checks
failed or the run is incomplete (not frozen, an input file changed, the
population differs from the run, the draw differs from the one on disk, or a
reply could not be used).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from eval.judging.openai_backend import API_KEY_ENV, BASE_URL_ENV, OpenAIResponsesModel
from graph.ontology import ONTOLOGY_VERSION
from ingest.provenance import git_state
from ingest.store import ChunkNotFound
from persist.cli import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE
from persist.cli import Stop as _Stop
from persist.config import PersistConfig
from persist.db import postgres_filings, postgres_texts
from persist.filings import Filing
from persist.labeller import build_request, label_pair
from persist.labeller import load_prompt as load_label_prompt
from persist.links import EXACT_TITLE
from persist.paths import OUTPUT_DIR
from persist.prompt import PromptError, load_prompt
from persist.sample import PersistDraw, Population, draw, population
from persist.score_files import (
    DRAW_NAME,
    FROZEN_NAME,
    LABEL_RUN_PREFIX,
    LABELS_NAME,
    FrozenRun,
    NotFrozen,
    frozen_run,
    write_draw,
)
from resolve.cli import cost_cap
from resolve.label import estimate_usd
from resolve.label_report import build_label_report
from resolve.labeller import LABEL_NOTE, LabelOutcome, LabelPrompt, LabelPromptError, shown_chunks
from resolve.labels import LabelFileError, append_labels, read_labels
from resolve.nodes import RISK_LABEL, InputError, InputFile, ResolveNode, load_nodes
from resolve.paths import CANDIDATES_DIR, next_numbered
from resolve.report import write_report
from resolve.sample import DrawnPair, Stratum
from retrieve.answer_model import AnswerModel, AnswerModelError, ApiResponse, ModelRequest
from retrieve.query_cache import BATCH_CACHE_DIR, CachedBatchEmbedder, CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel

WORKERS = 4
DEFAULT_MAX_COST_USD = 1.0
DATABASE_ENV = "DATABASE_URL"

OpenModel = Callable[[], AbstractContextManager[AnswerModel]]
ReadFilings = Callable[[str], Sequence[Filing]]
ReadTexts = Callable[[str, Sequence[str]], Mapping[str, str]]
logger = logging.getLogger(__name__)


class _NoEmbeddings:
    def embed_batch(self, texts, model, input_type):  # noqa: ANN001 - BatchEmbedder protocol
        raise CacheError("a risk factor is not embedded; run python -m persist.run first")


class _NoCalls:
    """Stands in for the labeller when every request is cached."""

    def complete(self, request: ModelRequest) -> ApiResponse:
        raise AssertionError("every request was cached, yet a labeller call was made")


class _CachedOnly:
    """persist.pipeline's embedder seam, reading the embedding cache and nothing else."""

    def __init__(self, cache_dir: Path, config: PersistConfig):
        self._cache = CachedBatchEmbedder(_NoEmbeddings(), cache_dir)
        self._config = config

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        model, input_type = self._config.embedding_model, self._config.embed_input_type
        if missing := self._cache.uncached(texts, model, input_type):
            raise _Stop(EXIT_RUN_ERROR, f"{len(missing)} risk factors are not embedded; run "
                                        "python -m persist.run first")
        return self._cache.embed(texts, model, input_type).vectors


def _openai_model() -> AbstractContextManager[AnswerModel]:
    return OpenAIResponsesModel.from_env()


@dataclass(frozen=True)
class LabelDeps:
    """Where labelling reads and writes and what it calls; tests swap these."""

    out_dir: Path = OUTPUT_DIR
    candidates_dir: Path = CANDIDATES_DIR
    embed_cache: Path = BATCH_CACHE_DIR
    response_cache: Path = DEFAULT_CACHE_DIR
    open_model: OpenModel = _openai_model
    read_filings: ReadFilings = postgres_filings
    read_texts: ReadTexts = postgres_texts
    commit: Callable[[], str] = git_state


def main(argv: Sequence[str] | None = None, deps: LabelDeps | None = None,
         config: PersistConfig | None = None) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        args = _parse_args(argv)
        url = _check_env()
        return _run(args.max_cost, url, deps or LabelDeps(), config or PersistConfig())
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m persist.label",
                                     description="Draw the persistence pairs and label them.")
    parser.add_argument("--max-cost", type=cost_cap, default=DEFAULT_MAX_COST_USD,
                        help=f"refuse a run estimated above this many USD "
                             f"(default {DEFAULT_MAX_COST_USD:g})")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


def _check_env() -> str:
    missing = [name for name in (API_KEY_ENV, DATABASE_ENV) if not os.environ.get(name)]
    if missing:
        raise _Stop(EXIT_RUN_ERROR, f"{', '.join(missing)} not set (run with: uv run "
                                    "--env-file .env python -m persist.label ...)")
    if os.environ.get(BASE_URL_ENV):
        raise _Stop(EXIT_RUN_ERROR, f"{BASE_URL_ENV} is set; unset it so filing text and the "
                                    "API key go only to OpenAI")
    return os.environ[DATABASE_ENV]


def _run(max_cost: float, url: str, deps: LabelDeps, config: PersistConfig) -> int:
    try:
        persist_version = load_prompt().prompt_version
        prompt = load_label_prompt()
    except (PromptError, LabelPromptError) as exc:
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
    run = _frozen(deps.out_dir, config, persist_version)
    factors, inputs = _factors(run, deps.candidates_dir)
    filings = _filings(url, deps.read_filings)
    try:
        pop = population(filings, factors, _CachedOnly(deps.embed_cache, config), config)
    except (CacheError, OSError, ValueError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot rebuild the population: {exc}") from exc
    _check_population(pop, run)
    drawn = draw(pop, config.config_hash)
    _record_draw(deps, drawn, {
        "commit": deps.commit(), "created_at": _now(), "frozen": FROZEN_NAME,
        "run_report": run.report_name, "links_sha256": run.links_sha256,
        "persist_prompt": persist_version, "label_prompt": prompt.prompt_version,
        "ontology_version": ONTOLOGY_VERSION,
        "inputs": [{"file": f.name, "sha256": f.sha256} for f in inputs]})
    by_key = {f.key: f for f in factors}
    pending = _pending(deps.out_dir, drawn)
    texts = _chunk_texts(deps.read_texts, url, [by_key[k] for p in pending for k in p.keys])
    filing_of = {f.accession_no: f for f in filings}
    outcomes = _label(pending, by_key, filing_of, texts, prompt, deps, max_cost)
    return _finish(deps, drawn, outcomes, prompt, inputs, config, run)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _frozen(out_dir: Path, config: PersistConfig, persist_version: str) -> FrozenRun:
    try:
        return frozen_run(out_dir, config.config_hash, persist_version)
    except NotFrozen as exc:
        raise _Stop(EXIT_CHECK_FAILED, str(exc)) from exc
    except (LabelFileError, ValueError) as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the frozen run: {exc}") from exc


def _factors(run: FrozenRun, candidates_dir: Path
             ) -> tuple[tuple[ResolveNode, ...], tuple[InputFile, ...]]:
    """The frozen run's risk factors, from its own input files, each checked by hash."""
    listed = (run.report.get("provenance") or {}).get("inputs") or []
    try:
        wanted = {i["file"]: i["sha256"] for i in listed}
        if not wanted:
            raise _Stop(EXIT_USAGE, f"{run.report_name} lists no input files")
        nodes, inputs = load_nodes([candidates_dir / name for name in wanted])
    except (KeyError, TypeError) as exc:
        raise _Stop(EXIT_USAGE, f"{run.report_name} lists its inputs badly: {exc}") from exc
    except InputError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the frozen run's inputs: {exc}") from exc
    if changed := [f.name for f in inputs if wanted.get(f.name) != f.sha256]:
        raise _Stop(EXIT_CHECK_FAILED, f"{len(changed)} input files changed since "
                                       f"{run.report_name}, such as {changed[0]}")
    return tuple(n for n in nodes if n.label == RISK_LABEL), inputs


def _filings(url: str, read_filings: ReadFilings) -> tuple[Filing, ...]:
    try:
        return tuple(read_filings(url))
    except psycopg.Error as exc:  # the message can quote the URL, so only the type is shown
        raise _Stop(EXIT_RUN_ERROR, f"could not read the filings: database error "
                                    f"({type(exc).__name__})") from exc


def _check_population(pop: Population, run: FrozenRun) -> None:
    """The candidate stratum must be the frozen run's exact-title links and adjudicated pairs,
    or the strata describe other settings than the scored links."""
    try:
        sent = {(c["earlier_key"], c["later_key"]) for c in run.report.get("candidates") or []}
    except (KeyError, TypeError) as exc:
        raise _Stop(EXIT_USAGE, f"{run.report_name} lists its candidates badly: {exc}") from exc
    exact = {(lk.earlier_key, lk.later_key) for lk in run.links if lk.method == EXACT_TITLE}
    candidates = {p.keys for p in pop.by_stratum.get(Stratum.CANDIDATE, ())}
    problems = [what for what, ok in (
        ("adjudicated pairs", pop.adjudicated == sent),
        ("candidate stratum", candidates == pop.adjudicated | exact)) if not ok]
    if problems:
        raise _Stop(EXIT_CHECK_FAILED, f"the rebuilt {' and '.join(problems)} differ from "
                                       f"{run.report_name}; the strata would not describe the "
                                       "scored links")


def _record_draw(deps: LabelDeps, drawn: PersistDraw, provenance: Mapping[str, object]) -> None:
    try:
        if write_draw(deps.out_dir / DRAW_NAME, drawn, provenance):
            logger.info("drew %d persistence pairs", len(drawn.pairs))
    except LabelFileError as exc:
        raise _Stop(EXIT_CHECK_FAILED, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot write the draw: {exc}") from exc


def _pending(out_dir: Path, drawn: PersistDraw) -> list[DrawnPair]:
    try:
        done = {label.keys for label in read_labels(out_dir / LABELS_NAME)}
    except LabelFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the labels: {exc}") from exc
    return [p for p in drawn.pairs if p.keys not in done]


def _chunk_texts(read_texts: ReadTexts, url: str,
                 factors: Sequence[ResolveNode]) -> Mapping[str, str]:
    wanted = sorted({c for f in factors for c in shown_chunks(f)})
    if not wanted:
        return {}
    try:
        texts = read_texts(url, wanted)
    except ChunkNotFound as exc:
        raise _Stop(EXIT_RUN_ERROR, f"chunk {exc} is not in Postgres; load the filing with "
                                    "python -m ingest.load") from exc
    except psycopg.Error as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not read the chunk texts: database error "
                                    f"({type(exc).__name__})") from exc
    return texts


def _label(pending: Sequence[DrawnPair], by_key: Mapping[str, ResolveNode],
           filing_of: Mapping[str, Filing], texts: Mapping[str, str], prompt: LabelPrompt,
           deps: LabelDeps, max_cost: float) -> list[LabelOutcome]:
    probe = CachedAnswerModel(_NoCalls(), deps.response_cache)
    try:
        requests = [build_request(prompt, by_key[p.key_a], by_key[p.key_b], filing_of,
                                  texts.__getitem__) for p in pending]
    except (KeyError, ValueError) as exc:  # a drawn key with no factor, a filing missing
        raise _Stop(EXIT_CHECK_FAILED, f"the draw does not match the factors: {exc}") from exc
    uncached = [r for r in requests if not probe.contains(r)]
    estimate = estimate_usd(uncached)
    print(f"{len(pending)} pairs to label, {len(uncached)} not cached; estimate "
          f"${estimate:.2f} (cap ${max_cost:.2f})")
    if estimate > max_cost:
        raise _Stop(EXIT_RUN_ERROR, f"the estimate ${estimate:.2f} is over --max-cost "
                                    f"${max_cost:.2f}; no call was made")
    opened = deps.open_model() if uncached else nullcontext(_NoCalls())
    out = deps.out_dir / LABELS_NAME
    outcomes: list[LabelOutcome] = []
    try:
        with opened as inner, ThreadPoolExecutor(WORKERS) as pool:
            model = CachedAnswerModel(inner, deps.response_cache)
            for outcome in pool.map(lambda p: label_pair(
                    p, by_key[p.key_a], by_key[p.key_b], filing_of, texts.__getitem__, model,
                    prompt), pending):
                if outcome.label is not None:
                    append_labels(out, [outcome.label])
                outcomes.append(outcome)
    except (AnswerModelError, CacheError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"labelling stopped after {len(outcomes)} pairs: {exc}"
                    ) from exc
    except LabelFileError as exc:
        raise _Stop(EXIT_CHECK_FAILED, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot write a label: {exc}") from exc
    return outcomes


def _finish(deps: LabelDeps, drawn: PersistDraw, outcomes: Sequence[LabelOutcome],
            prompt: LabelPrompt, inputs: Sequence[InputFile], config: PersistConfig,
            run: FrozenRun) -> int:
    try:
        labels = read_labels(deps.out_dir / LABELS_NAME)
        path = next_numbered(deps.out_dir, LABEL_RUN_PREFIX)
        report = build_label_report(drawn, labels, outcomes, prompt, inputs, config)
        report["provenance"] = {**report["provenance"], "commit": deps.commit(),
                                "frozen": FROZEN_NAME, "run_report": run.report_name}
        write_report(path, report)
    except LabelFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot write the label report: {exc}") from exc
    drawn_keys = {p.keys for p in drawn.pairs}
    unlabelled = len(drawn_keys - {lb.keys for lb in labels})
    print(f"{len(labels)} of {len(drawn.pairs)} pairs labelled ({report['counts']['same']} "
          f"same, {LABEL_NOTE}); {unlabelled} unlabelled, cost ${report['cost_usd']:.4f}; "
          f"wrote {path.name}")
    return EXIT_OK if not unlabelled else EXIT_CHECK_FAILED


if __name__ == "__main__":
    sys.exit(main())
