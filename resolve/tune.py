"""Tune the resolver on the labelled tuning set, then freeze it (Step 9, ticket 06; 9b).

Usage (needs ``DATABASE_URL``; ``--adjudicate`` also needs ``ANTHROPIC_API_KEY``
with ``ANTHROPIC_BASE_URL`` unset; every name embedding must be cached)::

    uv run --env-file .env python -m resolve.tune
    env -u ANTHROPIC_BASE_URL uv run --env-file .env python -m resolve.tune --adjudicate
    uv run --env-file .env python -m resolve.tune --freeze
    uv run --env-file .env python -m resolve.tune --freeze scoring2

For each config in `resolve.tuning.CONFIGS` it scores every same-label pair
(offline) and reports how many pairs would go to the adjudicator, how many of
the tuning set's true matches the rules find and how many non-matches they
flag. With ``--adjudicate`` the adjudicator also judges the tuning pairs the
chosen config (ResolveConfig's defaults) sends it, through the response cache,
after a cost estimate checked against ``--max-cost``. The report goes to
``benchmarks/resolution/tuning-report.json``.

``--freeze`` writes ``frozen.json`` once, holding the chosen config hash, the
label and adjudicator prompt versions and their models and efforts, with the
tuning report's hash. It needs a clean working tree and a tuning report made
from the current tuning labels, with an adjudicator check under the current
config, prompt and model.

``--freeze scoring2`` (Step 9b) writes ``frozen-2.json`` once for scoring
round 2, with the same guards, leaving ``frozen.json`` as round 1's record.
The tuning report may predate the group fields (``max_group_names``,
``group_rule``), since it compared candidate rules only; its chosen config
must then equal the current one without them. It also needs a passing
``tuning-check.json`` (``python -m resolve.check_tuning``) on a run under the
current config: no newly merged tuning pair labelled not same.

Every number rests on gpt-6-luna's labels: "agent-labelled (gpt-6-luna)".

Exit codes: 0 done; 2 bad arguments or a malformed input; 3 could not run (a
setting missing, an uncached embedding, the estimate over the cap, an API,
database or file error); 4 already frozen, no matching tuning report, or no
passing tuning check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

import psycopg

from ingest.atomic_json import write_json_atomic
from ingest.provenance import git_state
from ingest.store import ChunkNotFound
from ingest.store import resolve as resolve_chunk
from resolve.adjudicate import ClaudeAdjudicator
from resolve.candidates import ScoredPair, is_named
from resolve.cli import cost_cap
from resolve.config import ResolveConfig
from resolve.decisions import PairDecision
from resolve.embed import VoyageNodeEmbedder, embed_text
from resolve.estimate import estimate_cost
from resolve.label_report import now
from resolve.labeller import (
    LABEL_EFFORT,
    LABEL_MODEL,
    LABEL_NOTE,
    LabelPromptError,
    load_label_prompt,
)
from resolve.labels import (
    CHECK_REPORT_NAME,
    FROZEN_FILES,
    GROUP_CUT_SETS,
    TUNING_REPORT_NAME,
    Frozen,
    LabelFileError,
    labels_path,
    model_pin,
    read_json,
    read_labels,
    write_frozen,
)
from resolve.log import Stamp
from resolve.nodes import InputError, ResolveNode, load_nodes
from resolve.paths import CANDIDATES_DIR, OUTPUT_DIR, latest_candidates
from resolve.pipeline import Resolution, resolve_nodes
from resolve.prompt import PromptError, ResolvePrompt, load_prompt
from resolve.request import (
    ADJUDICATION_PRICES,
    ADJUDICATOR_EFFORT,
    ADJUDICATOR_MODEL,
    AdjudicateRequest,
    sent_chunks,
)
from resolve.sample import SCORING_SETS
from resolve.tuning import (
    CHOSEN,
    CONFIGS,
    AdjudicatorCheck,
    TunedLabel,
    adjudicator_check,
    check_to_dict,
    rule_check,
)
from retrieve.answer_model import API_KEY_ENV, AnswerModel, AnswerModelError, AnthropicAnswerModel
from retrieve.pricing import anthropic_cost
from retrieve.query_cache import BATCH_CACHE_DIR, CachedBatchEmbedder, CacheError
from retrieve.response_cache import DEFAULT_CACHE_DIR, CachedAnswerModel

DEFAULT_MAX_COST_USD = 0.5
WORKERS = 4
DATABASE_ENV = "DATABASE_URL"
BASE_URL_ENV = "ANTHROPIC_BASE_URL"
DIRTY = "+dirty"
EXIT_OK, EXIT_USAGE, EXIT_RUN_ERROR, EXIT_CHECK_FAILED = 0, 2, 3, 4
_NO_STAMP = Stamp(config_hash="", commit="", timestamp="")  # no merge event is written

OpenModel = Callable[[], AbstractContextManager[AnswerModel]]
ReadTexts = Callable[[str, Sequence[str]], Mapping[str, str]]


class _Stop(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class _NoCalls:
    """A Voyage stand-in: tuning reads name vectors from the cache only."""

    def embed_batch(self, texts, model, input_type):  # noqa: ANN001 - BatchEmbedder protocol
        raise CacheError("a name embedding is not cached; run python -m resolve.run first")


def _anthropic_model() -> AbstractContextManager[AnswerModel]:
    return AnthropicAnswerModel.from_env()


def _postgres_texts(url: str, chunk_ids: Sequence[str]) -> Mapping[str, str]:
    with psycopg.connect(url) as conn:
        return {c: resolve_chunk(conn, c) for c in chunk_ids}


@dataclass(frozen=True)
class TuneDeps:
    """Where tuning reads from and what it calls; tests swap these."""

    out_dir: Path = OUTPUT_DIR
    candidates_dir: Path = CANDIDATES_DIR
    embed_cache: Path = BATCH_CACHE_DIR
    response_cache: Path = DEFAULT_CACHE_DIR
    open_model: OpenModel = _anthropic_model
    read_texts: ReadTexts = _postgres_texts
    commit: Callable[[], str] = git_state


def main(argv: Sequence[str] | None = None, deps: TuneDeps | None = None) -> int:
    """CLI entry point; see the module docstring for the exit codes."""
    deps = deps or TuneDeps()
    try:
        args = _parse_args(argv)
        if args.freeze:
            return _freeze(deps, args.freeze)
        url = _env(DATABASE_ENV)
        nodes = _nodes(deps.candidates_dir)
        labels = _labels(deps.out_dir)
        rows, chosen = _rule_rows(nodes, labels, deps.embed_cache)
        adjudication = _adjudicate(nodes, labels, chosen, url, args.max_cost, deps) \
            if args.adjudicate else None
        _write_report(deps, rows, adjudication)
        return EXIT_OK
    except _Stop as stop:
        print(str(stop), file=sys.stderr)
        return stop.code


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m resolve.tune", description=__doc__.split(
        "\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adjudicate", action="store_true",
                        help="judge the tuning pairs the chosen config sends to the adjudicator")
    parser.add_argument("--max-cost", type=cost_cap, default=DEFAULT_MAX_COST_USD,
                        help=f"refuse adjudication estimated above this many USD "
                             f"(default {DEFAULT_MAX_COST_USD:g})")
    parser.add_argument("--freeze", nargs="?", const=SCORING_SETS[0], choices=SCORING_SETS,
                        help="write the freeze file of a scoring set (default scoring, "
                             "frozen.json; scoring2 writes frozen-2.json) from the chosen "
                             "config and current prompts")
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:
        raise _Stop(EXIT_USAGE, "bad arguments") from exc


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise _Stop(EXIT_RUN_ERROR, f"{name} not set (run with: uv run --env-file .env ...)")
    return value


def _nodes(candidates_dir: Path) -> tuple[ResolveNode, ...]:
    try:
        nodes, _ = load_nodes(latest_candidates(candidates_dir))
    except InputError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    return nodes


def _labels(out_dir: Path) -> tuple[TunedLabel, ...]:
    try:
        labels = read_labels(labels_path(out_dir, "tuning"))
    except LabelFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    if not labels:
        raise _Stop(EXIT_RUN_ERROR, "no tuning labels; run python -m resolve.label --set tuning")
    return tuple(TunedLabel(lb.key_a, lb.key_b, lb.same) for lb in labels)


def _resolve(nodes: Sequence[ResolveNode], config: ResolveConfig,
             embed_cache: Path) -> Resolution:
    """Pairs and pending pairs under *config*, with no adjudicator and name vectors cached."""
    embedder = None
    if config.top_k:
        cache = CachedBatchEmbedder(_NoCalls(), embed_cache)
        texts = [embed_text(n, config.embed_text) for n in nodes if is_named(n.label)]
        if missing := cache.uncached(texts, config.embedding_model, config.embed_input_type):
            raise _Stop(EXIT_RUN_ERROR, f"{len(missing)} names are not embedded as "
                                        f"{config.embed_text}; run python -m resolve.run first")
        embedder = VoyageNodeEmbedder(cache, config.embedding_model, config.embed_input_type)
    try:
        return resolve_nodes(nodes, embedder, None, config, stamp=_NO_STAMP)
    except (CacheError, OSError, ValueError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not score the pairs: {exc}") from exc


def _rule_rows(nodes: Sequence[ResolveNode], labels: Sequence[TunedLabel], embed_cache: Path
               ) -> tuple[list[dict[str, object]], Resolution]:
    rows, results = [], {}
    for name, config in CONFIGS.items():
        results[name] = _resolve(nodes, config, embed_cache)
        check = rule_check(results[name].pairs, len(results[name].pending), labels)
        print(f"{name:34} pending {check.pending:5}  matches found {check.matches_found}/"
              f"{check.matches}  non-matches flagged {check.non_matches_flagged}/"
              f"{check.non_matches}")
        rows.append({"name": name, "config": config.to_dict(), "config_hash": config.config_hash,
                     **check_to_dict(check)})
    return rows, results[CHOSEN]


def _adjudicate(nodes: Sequence[ResolveNode], labels: Sequence[TunedLabel], chosen: Resolution,
                url: str, max_cost: float, deps: TuneDeps) -> dict[str, object]:
    if os.environ.get(BASE_URL_ENV):
        raise _Stop(EXIT_RUN_ERROR, f"{BASE_URL_ENV} is set; unset it so filing text and the "
                                    "API key go only to Anthropic")
    _env(API_KEY_ENV)
    try:
        prompt = load_prompt()
    except PromptError as exc:
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc
    wanted = {k for lb in labels for k in (lb.keys, lb.keys[::-1])}
    pairs = [p for p in chosen.pending if (p.key_a, p.key_b) in wanted]
    by_key = {n.key: n for n in nodes}
    texts = _texts(deps.read_texts, url, [by_key[k] for p in pairs for k in (p.key_a, p.key_b)])
    try:
        client = deps.open_model()
    except AnswerModelError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot open the Anthropic client: {exc}") from exc
    with client as inner:
        cached = CachedAnswerModel(inner, deps.response_cache)
        adjudicator = ClaudeAdjudicator(cached, prompt, texts)
        requests = [adjudicator.request(by_key[p.key_a], by_key[p.key_b]) for p in pairs]
        _check_estimate([r for r in requests if not cached.contains(r)], len(pairs), max_cost)
        decisions = _judge_all(adjudicator, pairs, by_key)
    check = adjudicator_check({(d.key_a, d.key_b): d.merges for d in decisions}, labels)
    _print_check(check)
    return _adjudication_record(check, decisions, prompt)


def _judge_all(adjudicator: ClaudeAdjudicator, pairs: Sequence[ScoredPair],
               by_key: Mapping[str, ResolveNode]) -> list[PairDecision]:
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            return list(pool.map(
                lambda p: adjudicator.judge(p, by_key[p.key_a], by_key[p.key_b]), pairs))
    # judge() returns API failures as decisions and raises only for the cache, a file or text
    except (CacheError, OSError, ValueError) as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not adjudicate: {exc}") from exc


def _texts(read_texts: ReadTexts, url: str, nodes: Sequence[ResolveNode]) -> Mapping[str, str]:
    wanted = list(dict.fromkeys(c for n in nodes for c in sent_chunks(n)))
    try:
        return read_texts(url, wanted) if wanted else {}
    except ChunkNotFound as exc:
        raise _Stop(EXIT_RUN_ERROR, f"chunk {exc} is not in Postgres") from exc
    except psycopg.Error as exc:  # the message can quote the URL
        raise _Stop(EXIT_RUN_ERROR, f"could not read the chunks: {type(exc).__name__}") from exc


def _check_estimate(uncached: Sequence[AdjudicateRequest], pairs: int, max_cost: float) -> None:
    estimate = estimate_cost(uncached, WORKERS)
    print(f"{pairs} tuning pairs to adjudicate, {estimate.calls} not cached; estimate "
          f"${estimate.usd:.2f} (cap ${max_cost:.2f}, {ADJUDICATOR_MODEL})")
    if estimate.usd > max_cost:
        raise _Stop(EXIT_RUN_ERROR, f"the estimate ${estimate.usd:.2f} is over --max-cost "
                                    f"${max_cost:.2f}; no call was made")


def _print_check(check: AdjudicatorCheck) -> None:
    print(f"adjudicator agreed with the labels on {check.agreed} of {check.answered} pairs; "
          f"merged but labelled not same: {len(check.merged_but_not_same)}; labelled same "
          f"but not merged: {len(check.same_but_not_merged)}")


def _cost(decisions: Sequence[PairDecision]) -> float:
    return round(sum(anthropic_cost(ADJUDICATOR_MODEL, d.usage, ADJUDICATION_PRICES)
                     for d in decisions if d.usage is not None), 6)


def _adjudication_record(check: AdjudicatorCheck, decisions: Sequence[PairDecision],
                         prompt: ResolvePrompt) -> dict[str, object]:
    return {
        "model": ADJUDICATOR_MODEL, "effort": ADJUDICATOR_EFFORT,
        "prompt_version": prompt.prompt_version, "price_table": ADJUDICATION_PRICES.date,
        **check_to_dict(check),
        "answers": dict(Counter(d.answer.value if d.answer else "none" for d in decisions)),
        "failures": dict(Counter(d.failure for d in decisions if d.failure)),
        "output_tokens": sum(d.usage.output_tokens for d in decisions if d.usage),
        # what this run paid, and what the cached replies it replayed cost when first made
        "cost_usd": _cost([d for d in decisions if not d.from_cache]),
        "cost_usd_replayed": _cost([d for d in decisions if d.from_cache]),
        "decisions": [{"key_a": d.key_a, "key_b": d.key_b, "answer": d.answer and d.answer.value,
                       "merges": d.merges, "failure": d.failure, "reason": d.reason}
                      for d in decisions],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_report(deps: TuneDeps, rows: Sequence[Mapping[str, object]],
                  adjudication: Mapping[str, object] | None) -> None:
    path = deps.out_dir / TUNING_REPORT_NAME
    try:
        record = {
            "provenance": {"commit": deps.commit(), "created_at": now(), "note": LABEL_NOTE,
                           "labels_sha256": _sha256(labels_path(deps.out_dir, "tuning")),
                           "chosen": CHOSEN},
            "rules": list(rows), "adjudicator": adjudication,
        }
        write_json_atomic(path, record, indent=2)
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write {path.name}: {exc}") from exc
    print(f"wrote {path.name}")


def _current_pins() -> dict[str, str]:
    """The prompt versions, models and efforts frozen.json records."""
    try:
        return {"label": load_label_prompt().prompt_version,
                "resolve": load_prompt().prompt_version,
                "label_model": model_pin(LABEL_MODEL, LABEL_EFFORT),
                "resolve_model": model_pin(ADJUDICATOR_MODEL, ADJUDICATOR_EFFORT)}
    except (LabelPromptError, PromptError) as exc:
        raise _Stop(EXIT_RUN_ERROR, str(exc)) from exc


def _read_tuning_report(out_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
    """The tuning report's chosen row and adjudicator section; exit 2 when malformed."""
    path = out_dir / TUNING_REPORT_NAME
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _Stop(EXIT_CHECK_FAILED, f"{TUNING_REPORT_NAME} is missing; tune first") from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read {TUNING_REPORT_NAME}: {exc}") from exc
    except ValueError as exc:
        raise _Stop(EXIT_USAGE, f"{TUNING_REPORT_NAME} is not JSON") from exc
    if not isinstance(report, dict):
        raise _Stop(EXIT_USAGE, f"{TUNING_REPORT_NAME} is not a JSON object")
    rules = report.get("rules")
    chosen = next((r for r in rules if isinstance(r, dict) and r.get("name") == CHOSEN),
                  None) if isinstance(rules, list) else None
    adjudicator, provenance = report.get("adjudicator"), report.get("provenance")
    if chosen is None or not isinstance(adjudicator, dict) or not isinstance(provenance, dict):
        raise _Stop(EXIT_USAGE, f"{TUNING_REPORT_NAME} lacks the chosen row, the adjudicator "
                                "check or its provenance; run it again with --adjudicate")
    return chosen, {**adjudicator, "labels_sha256": provenance.get("labels_sha256")}


def _tuned_config_matches(chosen: Mapping[str, object], config: ResolveConfig,
                          set_name: str) -> bool:
    """The tuning report chose *config*, or, for round 2, the same candidate rules."""
    if chosen.get("config_hash") == config.config_hash:
        return True
    return set_name in GROUP_CUT_SETS and chosen.get("config") == config.candidate_rules()


def _tuning_check_sha(out_dir: Path, config: ResolveConfig) -> str:
    """The SHA-256 of a passing tuning check on a run under *config*; exit 4 otherwise."""
    path = out_dir / CHECK_REPORT_NAME
    if not path.exists():
        raise _Stop(EXIT_CHECK_FAILED, f"{CHECK_REPORT_NAME} is missing; run python -m "
                                       "resolve.check_tuning first")
    try:
        check = read_json(path)
    except LabelFileError as exc:
        raise _Stop(EXIT_USAGE, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read {CHECK_REPORT_NAME}: {exc}") from exc
    after = (check.get("provenance") or {}).get("after") or {}
    if after.get("config_hash") != config.config_hash:
        raise _Stop(EXIT_CHECK_FAILED, f"{CHECK_REPORT_NAME} checked a run under another "
                                       "config; run the tuning check on the current run")
    if check.get("newly_merged_not_same") != []:
        raise _Stop(EXIT_CHECK_FAILED, f"{CHECK_REPORT_NAME} lists newly merged pairs "
                                       "labelled not same; the user decides before freezing")
    return _sha256(path)


def _freeze(deps: TuneDeps, set_name: str) -> int:
    commit = deps.commit()
    if commit.endswith(DIRTY):
        raise _Stop(EXIT_CHECK_FAILED, "the working tree has changes; commit before freezing")
    name = FROZEN_FILES[set_name]
    if (deps.out_dir / name).exists():
        raise _Stop(EXIT_CHECK_FAILED, f"{name} exists; settings are frozen once")
    chosen, adjudicator = _read_tuning_report(deps.out_dir)
    config, pins = ResolveConfig(), _current_pins()
    try:
        labels_sha = _sha256(labels_path(deps.out_dir, "tuning"))
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"cannot read the tuning labels: {exc}") from exc
    stale = [what for what, ok in (
        ("config", _tuned_config_matches(chosen, config, set_name)),
        ("adjudicator prompt", adjudicator.get("prompt_version") == pins["resolve"]),
        ("adjudicator model", model_pin(adjudicator.get("model"), adjudicator.get("effort"))
         == pins["resolve_model"]),
        ("tuning labels", adjudicator.get("labels_sha256") == labels_sha),
    ) if not ok]
    if stale:
        raise _Stop(EXIT_CHECK_FAILED, f"{TUNING_REPORT_NAME} is stale ({', '.join(stale)}); "
                                       "run it again with --adjudicate")
    provenance = {"commit": commit, "created_at": now(), "config": config.to_dict(),
                  "tuning_report_sha256": _sha256(deps.out_dir / TUNING_REPORT_NAME)}
    if set_name in GROUP_CUT_SETS:
        provenance["tuning_check_sha256"] = _tuning_check_sha(deps.out_dir, config)
    try:
        write_frozen(deps.out_dir / name, Frozen(config.config_hash, pins), provenance)
    except LabelFileError as exc:
        raise _Stop(EXIT_CHECK_FAILED, str(exc)) from exc
    except OSError as exc:
        raise _Stop(EXIT_RUN_ERROR, f"could not write {name}: {exc}") from exc
    print(f"froze config {config.config_hash[:12]} in {name}, " + ", ".join(pins.values()))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
