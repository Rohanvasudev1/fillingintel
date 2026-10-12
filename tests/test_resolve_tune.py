"""Tuning numbers on the labelled tuning set: rules found and adjudicator agreement (ticket 06)."""
import hashlib
import json

import pytest

from resolve.candidates import Rule, ScoredPair, is_named
from resolve.config import ResolveConfig
from resolve.embed import embed_text
from resolve.labeller import load_label_prompt
from resolve.labels import labels_path, read_frozen
from resolve.nodes import load_nodes
from resolve.prompt import load_prompt
from resolve.tune import (
    EXIT_CHECK_FAILED,
    EXIT_OK,
    EXIT_RUN_ERROR,
    EXIT_USAGE,
    TuneDeps,
    main,
)
from resolve.tuning import CONFIGS, TunedLabel, adjudicator_check, rule_check
from retrieve.query_cache import CachedBatchEmbedder
from tests.resolve_helpers import NVDA_10K
from tests.test_resolve_run import FakeClaude, FakeVoyage


def _pair(a, b, *rules):
    return ScoredPair(key_a=a, key_b=b, label="Product", fuzzy=0.0, acronym=False,
                      shared_rare_words=(), embedding_rank=None, cosine=None,
                      rules=frozenset(rules))


LABELS = (TunedLabel("A", "B", same=True), TunedLabel("C", "D", same=True),
          TunedLabel("E", "F", same=False), TunedLabel("G", "H", same=False))


def test_rule_check_counts_matches_found_and_non_matches_flagged():
    pairs = (_pair("A", "B", Rule.FUZZY), _pair("C", "D"), _pair("E", "F", Rule.EMBEDDING),
             _pair("G", "H"), _pair("X", "Y", Rule.FUZZY))
    check = rule_check(pairs, pending=7, labels=LABELS)
    assert (check.pending, check.matches_found, check.matches) == (7, 1, 2)
    assert (check.non_matches_flagged, check.non_matches) == (1, 2)
    assert check.missed == (("C", "D"),)


def test_rule_check_finds_a_labelled_pair_in_either_key_order():
    check = rule_check((_pair("B", "A", Rule.FUZZY),), pending=1, labels=LABELS[:1])
    assert check.matches_found == 1


def test_a_labelled_pair_no_rule_scored_counts_as_missed():
    check = rule_check((), pending=0, labels=LABELS[:1])
    assert check.missed == (("A", "B"),)


def test_adjudicator_check_counts_agreement_on_the_pairs_it_answered():
    merged = {("A", "B"): True, ("C", "D"): False, ("E", "F"): True}  # G-H not adjudicated
    check = adjudicator_check(merged, LABELS)
    assert (check.answered, check.agreed) == (3, 1)
    assert check.merged_but_not_same == (("E", "F"),)
    assert check.same_but_not_merged == (("C", "D"),)


# ── python -m resolve.tune ────────────────────────────────────────────────────

NAMES = ("Groq", "Groq, Inc.", "Huawei", "Intel")


def _node_line(name, ordinal):
    key = f"Organization:{name.casefold()}"
    return {"kind": "node", "label": "Organization", "item": "x",
            "properties": {"key": key, "name": name},
            "evidence": [{"chunk_id": f"{NVDA_10K}:{ordinal:04d}", "confidence": "stated",
                          "extract_prompt": "extract/v2@00000000", "span": name}]}


def _label_line(a, b, same):
    return {"set_name": "tuning", "key_a": f"Organization:{a.casefold()}",
            "key_b": f"Organization:{b.casefold()}", "label": "Organization",
            "stratum": "candidate", "same": same, "quote_a": a, "chunk_id_a": "c",
            "quote_b": b, "chunk_id_b": "c", "quotes_verified": True, "reason": "test",
            "model": "gpt-6-luna", "effort": "high", "prompt_version": "label/v2@00000000"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real-0001")
    monkeypatch.setenv("DATABASE_URL", "postgresql://not-used")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    extract, out = tmp_path / "extract", tmp_path / "out"
    extract.mkdir()
    out.mkdir()
    (extract / f"{NVDA_10K}-run1.jsonl").write_text(
        "".join(json.dumps(_node_line(n, i + 10)) + "\n" for i, n in enumerate(NAMES)),
        encoding="utf-8")
    (out / "tuning-labels.jsonl").write_text(
        json.dumps(_label_line("Groq", "Groq, Inc.", True)) + "\n"
        + json.dumps(_label_line("Huawei", "Intel", False)) + "\n", encoding="utf-8")
    return tmp_path


def _seed_embeddings(env):
    nodes, _ = load_nodes([env / "extract" / f"{NVDA_10K}-run1.jsonl"])
    cache = CachedBatchEmbedder(FakeVoyage(), env / "embed")
    for config in CONFIGS.values():
        texts = [embed_text(n, config.embed_text) for n in nodes if is_named(n.label)]
        cache.embed(texts, config.embedding_model, config.embed_input_type)


def _deps(env, claude=None, commit="abc1234"):
    claude = claude or FakeClaude()
    return TuneDeps(out_dir=env / "out", candidates_dir=env / "extract",
                    embed_cache=env / "embed", response_cache=env / "responses",
                    open_model=lambda: _Context(claude),
                    read_texts=lambda url, ids: {i: f"Passage {i}." for i in ids},
                    commit=lambda: commit)


class _Context:
    def __init__(self, model):
        self._model = model

    def __enter__(self):
        return self._model

    def __exit__(self, *exc):
        return None


def _report(env):
    return json.loads((env / "out" / "tuning-report.json").read_text(encoding="utf-8"))


def test_uncached_embeddings_stop_tuning(env):
    assert main([], _deps(env)) == EXIT_RUN_ERROR
    assert not (env / "out" / "tuning-report.json").exists()


def test_tuning_reports_every_config_offline(env):
    _seed_embeddings(env)
    claude = FakeClaude()
    assert main([], _deps(env, claude)) == EXIT_OK
    report = _report(env)
    assert [r["name"] for r in report["rules"]] == list(CONFIGS)
    assert report["adjudicator"] is None and claude.calls == 0
    assert report["provenance"]["note"] == "agent-labelled (gpt-6-luna)"


def test_adjudication_refuses_a_base_url(env, monkeypatch):
    _seed_embeddings(env)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    assert main(["--adjudicate"], _deps(env)) == EXIT_RUN_ERROR


def test_adjudication_over_the_cap_makes_no_call(env):
    _seed_embeddings(env)
    claude = FakeClaude()
    assert main(["--adjudicate", "--max-cost", "0"], _deps(env, claude)) == EXIT_RUN_ERROR
    assert claude.calls == 0


def test_a_negative_cap_is_bad_arguments(env):
    assert main(["--max-cost", "-1"], _deps(env)) == EXIT_USAGE


def test_adjudication_records_agreement_and_spend(env):
    _seed_embeddings(env)
    claude = FakeClaude(answer="distinct")
    assert main(["--adjudicate"], _deps(env, claude)) == EXIT_OK
    adjudicator = _report(env)["adjudicator"]
    assert adjudicator["model"] == "claude-haiku-5-5"
    # Groq / Groq, Inc. merge deterministically, so only Huawei / Intel is adjudicated
    assert (adjudicator["answered"], adjudicator["agreed"]) == (claude.calls, claude.calls)
    assert adjudicator["cost_usd"] > 0 and adjudicator["cost_usd_replayed"] == 0
    assert main(["--adjudicate"], _deps(env, claude)) == EXIT_OK  # replayed from the cache
    replayed = _report(env)["adjudicator"]
    assert replayed["cost_usd"] == 0 and replayed["cost_usd_replayed"] == adjudicator["cost_usd"]


def _tuning_report(env, **changes):
    labels = labels_path(env / "out", "tuning").read_bytes()
    report = {"provenance": {"labels_sha256": hashlib.sha256(labels).hexdigest()},
              "rules": [{"name": "chosen", "config_hash": ResolveConfig().config_hash}],
              "adjudicator": {"prompt_version": load_prompt().prompt_version,
                              "model": "claude-haiku-5-5", "effort": "high"}}
    for path, value in changes.items():
        section, field = path.split("__")
        target = report[section][0] if section == "rules" else report[section]
        target[field] = value
    (env / "out" / "tuning-report.json").write_text(json.dumps(report), encoding="utf-8")


def test_freeze_needs_a_tuning_report(env):
    assert main(["--freeze"], _deps(env)) == EXIT_CHECK_FAILED
    assert not (env / "out" / "frozen.json").exists()


def test_freeze_refuses_a_malformed_report(env):
    (env / "out" / "tuning-report.json").write_text("[]", encoding="utf-8")
    assert main(["--freeze"], _deps(env)) == EXIT_USAGE


@pytest.mark.parametrize("change", [
    {"rules__config_hash": "0" * 64},
    {"adjudicator__prompt_version": "resolve/v1@00000000"},
    {"adjudicator__model": "claude-sonnet-5-5"},
    {"provenance__labels_sha256": "0" * 64},
])
def test_freeze_refuses_a_stale_report(env, change):
    _tuning_report(env, **change)
    assert main(["--freeze"], _deps(env)) == EXIT_CHECK_FAILED
    assert not (env / "out" / "frozen.json").exists()


def test_freeze_refuses_a_dirty_tree(env):
    _tuning_report(env)
    assert main(["--freeze"], _deps(env, commit="abc1234+dirty")) == EXIT_CHECK_FAILED


def test_freeze_writes_the_chosen_settings_once(env):
    _tuning_report(env)
    assert main(["--freeze"], _deps(env)) == EXIT_OK
    frozen = read_frozen(env / "out" / "frozen.json")
    assert frozen.config_hash == ResolveConfig().config_hash
    assert frozen.prompts == {"label": load_label_prompt().prompt_version,
                              "resolve": load_prompt().prompt_version,
                              "label_model": "gpt-6-luna@high",
                              "resolve_model": "claude-haiku-5-5@high"}
    assert main(["--freeze"], _deps(env)) == EXIT_CHECK_FAILED


# ── Freezing for scoring round 2 (Step 9b) ────────────────────────────────────

def _step9_row(env):
    """A tuning report from Step 9: the same candidate rules, before the group fields."""
    _tuning_report(env, rules__config_hash="0" * 64,
                   rules__config=ResolveConfig().candidate_rules())


def _tuning_check(env, config_hash=None, not_same=()):
    check = {"provenance": {"after": {"run": "run3.json",
                                      "config_hash": config_hash or ResolveConfig().config_hash}},
             "newly_merged_not_same": [{"key_a": a, "key_b": b, "same": False}
                                       for a, b in not_same]}
    (env / "out" / "tuning-check.json").write_text(json.dumps(check), encoding="utf-8")


def test_freeze_for_round_two_writes_frozen_2_once(env):
    _step9_row(env)
    _tuning_check(env)
    (env / "out" / "frozen.json").write_text("{}", encoding="utf-8")  # round 1's, left alone
    assert main(["--freeze", "scoring2"], _deps(env)) == EXIT_OK
    frozen = read_frozen(env / "out" / "frozen-2.json")
    assert frozen.config_hash == ResolveConfig().config_hash
    assert (env / "out" / "frozen.json").read_text(encoding="utf-8") == "{}"
    provenance = json.loads((env / "out" / "frozen-2.json").read_text())["provenance"]
    assert len(provenance["tuning_check_sha256"]) == 64
    assert main(["--freeze", "scoring2"], _deps(env)) == EXIT_CHECK_FAILED


@pytest.mark.parametrize("check", [
    None,  # no tuning check
    {"config_hash": "0" * 64},  # the check ran on another config
    {"not_same": [("Product:a", "Product:b")]},  # a newly merged pair labelled not same
])
def test_freeze_for_round_two_needs_a_passing_tuning_check(env, check):
    _step9_row(env)
    if check is not None:
        _tuning_check(env, **check)
    assert main(["--freeze", "scoring2"], _deps(env)) == EXIT_CHECK_FAILED
    assert not (env / "out" / "frozen-2.json").exists()


def test_freeze_for_round_two_refuses_other_candidate_rules(env):
    rules = {**ResolveConfig().candidate_rules(), "fuzzy_cutoff": 90.0}
    _tuning_report(env, rules__config_hash="0" * 64, rules__config=rules)
    _tuning_check(env)
    assert main(["--freeze", "scoring2"], _deps(env)) == EXIT_CHECK_FAILED


def test_freeze_for_round_two_refuses_a_dirty_tree(env):
    _step9_row(env)
    _tuning_check(env)
    assert main(["--freeze", "scoring2"], _deps(env, commit="abc1234+dirty")) == \
        EXIT_CHECK_FAILED


def test_a_listed_pair_the_user_accepts_lets_round_two_freeze_and_is_recorded(env):
    _step9_row(env)
    _tuning_check(env, not_same=[("Regulation:a", "Regulation:b")])
    accept = ["--freeze", "scoring2", "--accept-pair", "Regulation:b", "Regulation:a"]
    assert main(accept, _deps(env)) == EXIT_OK
    provenance = json.loads((env / "out" / "frozen-2.json").read_text())["provenance"]
    assert provenance["accepted_not_same"] == [["Regulation:a", "Regulation:b"]]


@pytest.mark.parametrize("pairs", [
    [],  # the listed pair is not accepted
    [["Regulation:a", "Regulation:c"]],  # another pair
    [["Regulation:a", "Regulation:b"], ["Regulation:a", "Regulation:c"]],  # one too many
])
def test_accepted_pairs_must_match_the_listed_pairs_exactly(env, pairs):
    _step9_row(env)
    _tuning_check(env, not_same=[("Regulation:a", "Regulation:b")])
    args = ["--freeze", "scoring2", *(a for p in pairs for a in ("--accept-pair", *p))]
    assert main(args, _deps(env)) == EXIT_CHECK_FAILED
    assert not (env / "out" / "frozen-2.json").exists()


def test_accepting_a_pair_needs_a_round_two_freeze(env):
    assert main(["--accept-pair", "Regulation:a", "Regulation:b"], _deps(env)) == EXIT_USAGE
