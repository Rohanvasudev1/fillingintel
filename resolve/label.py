"""Draw a set of pairs and have gpt-6-luna label them (Step 9, ticket 04; Step 9b).

Usage (needs ``OPENAI_API_KEY`` and ``DATABASE_URL``, with ``OPENAI_BASE_URL``
unset; run ``python -m resolve.run`` first, so every name embedding is cached)::

    uv run --env-file .env python -m resolve.label --set tuning
    uv run --env-file .env python -m resolve.label --set scoring --max-cost 2
    uv run --env-file .env python -m resolve.label --set scoring2 --max-cost 1

The command reads the latest candidates file of each accession, scores every
same-label pair under the current config (embeddings from the cache only),
and draws the set (resolve.sample). The draw goes to
``benchmarks/resolution/{set}-draw.json`` once; a later run must draw the same
pairs. A scoring draw leaves out the pairs of the sets drawn before it
(resolve.sample.EXCLUDED_SETS) and refuses to run unless its freeze file
(``frozen.json`` for ``scoring``, ``frozen-2.json`` for ``scoring2``) holds
the current config hash, label prompt version and the adjudicator prompt
version of the latest run report.

It then reads each unlabelled pair's chunk texts from Postgres, prints a cost
estimate for the calls not in the response cache, stops if it is over
``--max-cost`` (default $5), and labels the pairs 4 at a time. Each label is
appended to ``{set}-labels.jsonl`` as it arrives, and a pair already labelled
is never sent again. The run's counts, tokens and cost go to
``{set}-label-run{N}.json``. Every figure built on these labels is
"agent-labelled (gpt-6-luna)".

Exit codes: 0 every drawn pair is labelled; 2 bad arguments or a malformed
input, draw or label file; 3 could not run (a setting missing, Postgres
unreachable, an uncached embedding, a missing earlier draw, the estimate over
the cap, an API, cache or file error); 4 checks failed or the run is
incomplete (the draw differs from the one on disk, the settings are not
frozen, or a reply could not be used).
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
from pathlib import Path

import psycopg

from eval.judging.openai_backend import (
    API_KEY_ENV,
    BASE_URL_ENV,
    OpenAIResponsesModel,
    OpenAIUsage,
)
from eval.judging.openai_judge import OpenAIJudgeRequest
from graph.ontology import ONTOLOGY_VERSION
from ingest.chunker import count_tokens
from ingest.provenance import git_state
from ingest.store import resolve as resolve_chunk
from resolve.candidates import ScoredPair, is_named, score_pairs
from resolve.cli import cost_cap
from resolve.config import ResolveConfig
from resolve.embed import VoyageNodeEmbedder, embed_text
from resolve.label_report import build_label_report, now
from resolve.labeller import (
    LABEL_EFFORT,
    LABEL_MODEL,
    LabelOutcome,
    LabelPrompt,
    LabelPromptError,
    build_label_request,
    label_pair,
    load_label_prompt,
    shown_chunks,
)
from resolve.labels import (
    FROZEN_FILES,
    LabelFileError,
    append_labels,
    draw_path,
    frozen_mismatches,
    labels_path,
    model_pin,
    read_draw,
    read_frozen,
    read_json,
    read_labels,
    write_draw,
)
from resolve.nodes import InputError, InputFile, ResolveNode, load_nodes
from resolve.paths import (
    CANDIDATES_DIR,
    OUTPUT_DIR,
    latest_candidates,
    latest_report,
    next_numbered,
)
from resolve.report import write_report
from resolve.sample import EXCLUDED_SETS, SETS, Draw, DrawnPair, draw
from resolve.scoring import LABEL_NOTE
from retrieve.answer_model import AnswerModel, AnswerModelError, ApiResponse, ModelRequest
from retrieve.pricing import openai_cost
from retrieve.query_cache import BATCH_CACHE_DIR, CachedBatchEmbedder, CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUN_ERROR = 3
EXIT_CHECK_FAILED = 4
WORKERS = 4
DEFAULT_MAX_COST_USD = 5.0
ASSUMED_OUTPUT_TOKENS = 4_000  # reasoning plus JSON at effort high, before any reply is seen
DATABASE_ENV = "DATABASE_URL"

OpenModel = Callable[[], AbstractContextManager[AnswerModel]]
ReadChunks = Callable[[str, Sequence[str]], Mapping[str, str]]  # URL, chunk IDs -> texts
logger = logging.getLogger(__name__)


class _Stop(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class _NoEmbeddings:
    def embed_batch(self, texts, model, input_type):
        raise AssertionError("an embedding was uncached after the check")


class _NoCalls:
    """Stands in for the labeller when every request is cached."""

    def complete(self, request: ModelRequest) -> ApiResponse:
        raise AssertionError("every request was cached, yet a labeller call was made")


def _read_chunks(url: str, chunk_ids: Sequence[str]) -> Mapping[str, str]:
    with psycopg.connect(url) as conn:
        return {c: resolve_chunk(conn, c) for c in chunk_ids}


def _openai_model() -> AbstractContextManager[AnswerModel]:
    return OpenAIResponsesModel.from_env()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m resolve.label",
                                     description="Draw a set of pairs and label them.")
    parser.add_argument("--set", dest="set_name", required=True, choices=SETS)
    parser.add_argument("--max-cost", type=cost_cap, default=DEFAULT_MAX_COST_USD,
                        help=f"refuse a run estimated above this many USD "
                             f"(default {DEFAULT_MAX_COST_USD:g})")
    parser.add_argument("--candidates", type=Path, nargs="+",
                        help="candidates files (default: the latest run of each accession)")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


@dataclass(frozen=True)
class _Paths:
    candidates_dir: Path
    out_dir: Path
    embed_cache: Path
    response_cache: Path


def main(argv: Sequence[str] | None = None, *, candidates_dir: Path = CANDIDATES_DIR,
         out_dir: Path = OUTPUT_DIR, embed_cache: Path = BATCH_CACHE_DIR,
         response_cache: Path = DEFAULT_CACHE_DIR, open_model: OpenModel = _openai_model,
         read_chunks: ReadChunks = _read_chunks, config: ResolveConfig | None = None) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        args = _parse_args(argv)
        url = _check_env()
        paths = _Paths(candidates_dir, out_dir, embed_cache, response_cache)
        return _run(args, url, paths, config or ResolveConfig(), open_model, read_chunks)
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def _check_env() -> str:
    missing = [name for name in (API_KEY_ENV, DATABASE_ENV) if not os.environ.get(name)]
    if missing:
        raise _Stop(EXIT_RUN_ERROR, f"{', '.join(missing)} not set (run with: uv run "
                                    "--env-file .env python -m resolve.label ...)")
    if os.environ.get(BASE_URL_ENV):
        raise _Stop(EXIT_RUN_ERROR, f"{BASE_URL_ENV} is set; unset it so filing text and the "
                                    "API key go only to OpenAI")
    return os.environ[DATABASE_ENV]


def _run(args: argparse.Namespace, url: str, paths: _Paths, config: ResolveConfig,
         open_model: OpenModel, read_chunks: ReadChunks) -> int:
    set_name = args.set_name
    try:
        prompt = load_label_prompt()
    except LabelPromptError as exc:
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
    files = args.candidates or latest_candidates(paths.candidates_dir)
    if not files:
        raise _Stop(EXIT_USAGE, "no candidates files; run python -m extract.run first")
    try:
        nodes, inputs = load_nodes(files)
    except InputError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    frozen = _check_frozen(paths.out_dir, FROZEN_FILES[set_name], config, prompt) \
        if set_name in FROZEN_FILES else None
    drawn = draw(_pairs(nodes, config, paths.embed_cache), set_name, config,
                 exclude=_excluded_pairs(paths.out_dir, set_name))
    _record_draw(paths.out_dir, drawn, {
        "commit": git_state(), "created_at": now(), "label_prompt": prompt.prompt_version,
        "ontology_version": ONTOLOGY_VERSION, "frozen": frozen,
        "inputs": [{"file": f.name, "sha256": f.sha256} for f in inputs]})
    by_key = {n.key: n for n in nodes}
    pending = _pending(paths.out_dir, drawn)
    texts = _chunk_texts(read_chunks, url, [by_key[k] for p in pending for k in p.keys])
    outcomes = _label(pending, set_name, by_key, texts, prompt, paths, open_model,
                      args.max_cost)
    return _finish(paths.out_dir, drawn, outcomes, prompt, inputs, config)


def _check_frozen(out_dir: Path, name: str, config: ResolveConfig,
                  prompt: LabelPrompt) -> dict[str, object]:
    """The settings frozen in *name*, after checking the config, prompts and latest run."""
    try:
        frozen = read_frozen(out_dir / name)
        report_path = latest_report(out_dir)
        report = read_json(report_path) if report_path is not None else None
    except (LabelFileError, OSError) as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    if frozen is None:
        raise _Stop(EXIT_CHECK_FAILED, f"{name} is missing; a scoring set is drawn only "
                                       "after the rules and prompts are frozen")
    if report is None:
        raise _Stop(EXIT_RUN_ERROR, "no run report; run python -m resolve.run first")
    adjudication = report.get("adjudication") or {}
    current = {"label": prompt.prompt_version,
               "resolve": adjudication.get("prompt_version") or "none",
               "label_model": model_pin(LABEL_MODEL, LABEL_EFFORT),
               "resolve_model": model_pin(adjudication.get("model"), adjudication.get("effort"))}
    problems = list(frozen_mismatches(frozen, config.config_hash, current))
    run_hash = (report.get("provenance") or {}).get("config_hash")
    if run_hash != config.config_hash:
        problems.append(f"{report_path.name} ran under config {str(run_hash)[:12]}, "
                        f"not {config.config_hash[:12]}")
    if problems:
        raise _Stop(EXIT_CHECK_FAILED, "not frozen: " + "; ".join(problems))
    return {"file": name, "config_hash": frozen.config_hash, "prompts": dict(frozen.prompts),
            "run_report": report_path.name}


def _pairs(nodes: Sequence[ResolveNode], config: ResolveConfig,
           embed_cache: Path) -> tuple[ScoredPair, ...]:
    """Every scored pair, with name vectors read from the cache only."""
    named = [n for n in nodes if is_named(n.label)]
    vectors: dict[str, Sequence[float]] = {}
    if config.top_k and named:
        cache = CachedBatchEmbedder(_NoEmbeddings(), embed_cache)
        texts = [embed_text(n, config.embed_text) for n in named]
        if missing := cache.uncached(texts, config.embedding_model, config.embed_input_type):
            raise _Stop(EXIT_RUN_ERROR, f"{len(missing)} names are not embedded yet; run "
                                        "python -m resolve.run first")
        try:
            embedded = VoyageNodeEmbedder(cache, config.embedding_model,
                                          config.embed_input_type).embed(texts)
        except (CacheError, OSError) as exc:
            raise _Stop(EXIT_RUN_ERROR, f"cannot read the embedding cache: {exc}") from exc
        vectors = {n.key: v for n, v in zip(named, embedded, strict=True)}
    try:
        return score_pairs(nodes, vectors, config)
    except ValueError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot score the pairs: {exc}") from exc


def _excluded_pairs(out_dir: Path, set_name: str) -> set[tuple[str, str]]:
    """The pairs of every set *set_name* leaves out; each of those draws must exist."""
    excluded: set[tuple[str, str]] = set()
    for earlier in EXCLUDED_SETS.get(set_name, ()):
        path = draw_path(out_dir, earlier)
        if not path.exists():
            raise _Stop(EXIT_RUN_ERROR, f"no {earlier} draw; the {set_name} set leaves out the "
                                        f"{earlier} pairs, so label the {earlier} set first")
        try:
            excluded |= {p.keys for p in read_draw(path).pairs}
        except (LabelFileError, OSError) as exc:
            raise _Stop(EXIT_USAGE, str(exc)) from exc
    return excluded


def _record_draw(out_dir: Path, drawn: Draw, provenance: Mapping[str, object]) -> None:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if write_draw(draw_path(out_dir, drawn.set_name), drawn, provenance):
            logger.info("drew %d %s pairs", len(drawn.pairs), drawn.set_name)
    except LabelFileError as exc:
        raise _Stop(EXIT_CHECK_FAILED, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot write the draw: {exc}") from exc


def _pending(out_dir: Path, drawn: Draw) -> list[DrawnPair]:
    try:
        done = {label.keys for label in read_labels(labels_path(out_dir, drawn.set_name))}
    except LabelFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the labels: {exc}") from exc
    return [p for p in drawn.pairs if p.keys not in done]


def _chunk_texts(read_chunks: ReadChunks, url: str,
                 nodes: Sequence[ResolveNode]) -> Mapping[str, str]:
    wanted = list(dict.fromkeys(c for n in nodes for c in shown_chunks(n)))
    if not wanted:
        return {}
    try:
        texts = read_chunks(url, wanted)
    except (psycopg.Error, LookupError) as exc:  # the message can quote the URL
        raise _Stop(EXIT_RUN_ERROR, f"could not read the chunk texts: {type(exc).__name__}"
                    ) from exc
    if missing := [c for c in wanted if c not in texts]:
        raise _Stop(EXIT_RUN_ERROR, f"{len(missing)} chunks are not in Postgres, such as "
                                    f"{missing[0]}; load them with python -m ingest.load")
    return texts


def estimate_usd(requests: Sequence[OpenAIJudgeRequest]) -> float:
    """The cost of *requests*, counting input with cl100k_base and assuming each reply's size."""
    usages = [OpenAIUsage(count_tokens(r.prompt) + count_tokens(r.schema), 0, 0,
                          ASSUMED_OUTPUT_TOKENS, 0) for r in requests]
    return sum(openai_cost(LABEL_MODEL, u) for u in usages)


def _requests(pending: Sequence[DrawnPair], by_key: Mapping[str, ResolveNode],
              texts: Mapping[str, str], prompt: LabelPrompt) -> list[OpenAIJudgeRequest]:
    try:
        return [build_label_request(prompt, by_key[p.key_a], by_key[p.key_b], texts.__getitem__)
                for p in pending]
    except (KeyError, ValueError) as exc:  # a drawn key with no node, or two labels
        raise _Stop(EXIT_CHECK_FAILED, f"the draw does not match the nodes: {exc}") from exc


def _label(pending: Sequence[DrawnPair], set_name: str, by_key: Mapping[str, ResolveNode],
           texts: Mapping[str, str], prompt: LabelPrompt, paths: _Paths,
           open_model: OpenModel, max_cost: float) -> list[LabelOutcome]:
    probe = CachedAnswerModel(_NoCalls(), paths.response_cache)
    uncached = [r for r in _requests(pending, by_key, texts, prompt) if not probe.contains(r)]
    estimate = estimate_usd(uncached)
    print(f"{set_name}: {len(pending)} pairs to label, {len(uncached)} not cached; estimate "
          f"${estimate:.2f} (cap ${max_cost:.2f})")
    if estimate > max_cost:
        raise _Stop(EXIT_RUN_ERROR, f"the estimate ${estimate:.2f} is over --max-cost "
                                    f"${max_cost:.2f}; no call was made")
    opened = open_model() if uncached else nullcontext(_NoCalls())
    out = labels_path(paths.out_dir, set_name)
    outcomes: list[LabelOutcome] = []
    try:
        with opened as inner, ThreadPoolExecutor(WORKERS) as pool:
            model = CachedAnswerModel(inner, paths.response_cache)
            for outcome in pool.map(lambda p: label_pair(
                    p, set_name, by_key[p.key_a], by_key[p.key_b], texts.__getitem__, model,
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


def _finish(out_dir: Path, drawn: Draw, outcomes: Sequence[LabelOutcome], prompt: LabelPrompt,
            inputs: Sequence[InputFile], config: ResolveConfig) -> int:
    try:
        labels = read_labels(labels_path(out_dir, drawn.set_name))
        path = next_numbered(out_dir, f"{drawn.set_name}-label-run")
        report = build_label_report(drawn, labels, outcomes, prompt, inputs, config)
        write_report(path, report)
    except LabelFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot write the label report: {exc}") from exc
    unlabelled = len(drawn.pairs) - len(labels)
    print(f"{drawn.set_name}: {len(labels)} of {len(drawn.pairs)} pairs labelled "
          f"({report['counts']['same']} same, {LABEL_NOTE}); {unlabelled} unlabelled, "
          f"cost ${report['cost_usd']:.4f}; wrote {path.name}")
    return EXIT_OK if not unlabelled else EXIT_CHECK_FAILED


if __name__ == "__main__":
    sys.exit(main())
