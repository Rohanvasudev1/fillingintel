"""`python -m resolve.label` and the label files: draws, resumes, caps and exit codes (Step 9).

Candidates files use extract.run's line format with NVDA run 1 names. Chunk
text and labeller replies are fakes; recorded replies are replayed in
test_resolve_label_recorded.py.
"""
import json
import re

import pytest

from resolve.config import ResolveConfig
from resolve.label import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE, main
from resolve.labeller import load_label_prompt
from resolve.labels import (
    FROZEN_NAME,
    Frozen,
    LabelFileError,
    append_labels,
    draw_path,
    labels_path,
    read_draw,
    read_labels,
    write_frozen,
)
from resolve.sample import Stratum
from retrieve.answer_model import ApiResponse
from tests.openai_fixtures import RECORDED, with_text
from tests.resolve_helpers import NVDA_10K

CONFIG = ResolveConfig(top_k=0)  # no embeddings needed
DRAWN = 16
NAMES = ["Groq", "Groq, Inc.", "Huawei", "Intel", "Samsung", "TSMC", "Microsoft", "Meta"]


def _node(name, ordinal):
    key = f"Organization:{name.casefold()}"
    return {"kind": "node", "label": "Organization", "item": "x",
            "properties": {"key": key, "name": name},
            "evidence": [{"chunk_id": f"{NVDA_10K}:{ordinal:04d}", "confidence": "stated",
                          "extract_prompt": "extract/v2@00000000", "span": name}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    extract = tmp_path / "extract"
    extract.mkdir()
    (extract / f"{NVDA_10K}-run1.jsonl").write_text(
        "".join(json.dumps(_node(n, i + 10)) + "\n" for i, n in enumerate(NAMES)),
        encoding="utf-8")
    return tmp_path


def _chunks(url, chunk_ids):
    return {c: f"Passage {c} names {NAMES[int(c[-4:]) - 10]} in full." for c in chunk_ids}


class FakeLabeller:
    """Says same for the two Groq names; quotes each node's own chunk."""

    def __init__(self):
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def complete(self, request):
        self.calls += 1
        prompt = request.prompt
        node_b = prompt.index("### Node B")
        a = re.search(r"^Chunk (\S+):$", prompt[:node_b], re.M)[1]
        b = re.search(r"^Chunk (\S+):$", prompt[node_b:], re.M)[1]
        names = re.findall(r"^Name: (.+)$", prompt, re.M)
        reply = {"reason": "test", "quote_a": f"names {names[0]}", "chunk_id_a": a,
                 "quote_b": f"names {names[1]}", "chunk_id_b": b,
                 "same": {*names} == {"Groq", "Groq, Inc."}}
        return ApiResponse(body=with_text(RECORDED, json.dumps(reply)), api_ms=1.0)


def _main(root, *args, labeller=None, config=CONFIG):
    labeller = labeller or FakeLabeller()
    return main(["--set", "tuning", *args], candidates_dir=root / "extract", out_dir=root / "out",
                embed_cache=root / "embed", response_cache=root / "responses",
                open_model=lambda: labeller, read_chunks=_chunks, config=config)


def test_a_tuning_run_draws_labels_and_reports(env):
    labeller = FakeLabeller()
    assert _main(env, labeller=labeller) == EXIT_OK
    drawn = read_draw(draw_path(env / "out", "tuning"))
    labels = read_labels(labels_path(env / "out", "tuning"))
    # 8 names give 28 pairs: the Groq pair is a candidate, and 15 of the other 27 are drawn
    assert len(drawn.pairs) == len(labels) == labeller.calls == DRAWN
    assert drawn.populations == {Stratum.CANDIDATE: 1, Stratum.NEAR: 0, Stratum.REST: 27}
    assert sum(lb.same for lb in labels) == 1
    assert all(lb.quotes_verified for lb in labels)
    report = json.loads((env / "out" / "tuning-label-run1.json").read_text(encoding="utf-8"))
    assert report["provenance"]["note"] == "agent-labelled (gpt-6-luna)"
    assert report["counts"]["labelled"] == DRAWN
    assert report["provenance"]["label_prompt"]["version"] == load_label_prompt().prompt_version


def test_a_rerun_labels_nothing_again_and_makes_no_call(env):
    assert _main(env) == EXIT_OK
    again = FakeLabeller()
    assert _main(env, labeller=again) == EXIT_OK
    assert again.calls == 0
    assert len(read_labels(labels_path(env / "out", "tuning"))) == DRAWN


def test_a_stopped_run_resumes_from_the_cache_without_relabelling(env):
    assert _main(env) == EXIT_OK
    path = labels_path(env / "out", "tuning")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[:10]), encoding="utf-8")  # as if it stopped after 10
    again = FakeLabeller()
    assert _main(env, labeller=again) == EXIT_OK
    assert again.calls == 0  # the other replies are in the response cache
    assert len(read_labels(path)) == DRAWN


def test_a_changed_draw_is_refused(env):
    assert _main(env) == EXIT_OK
    with (env / "extract" / f"{NVDA_10K}-run1.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_node("Qualcomm", 18)) + "\n")
    assert _main(env) == EXIT_CHECK_FAILED


def test_an_estimate_over_the_cap_makes_no_call(env):
    labeller = FakeLabeller()
    assert _main(env, "--max-cost", "0", labeller=labeller) == EXIT_RUN_ERROR
    assert labeller.calls == 0


@pytest.mark.parametrize("name, value", [("OPENAI_API_KEY", None), ("DATABASE_URL", None),
                                         ("OPENAI_BASE_URL", "https://example.test/v1")])
def test_settings_are_checked_before_anything_runs(env, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    assert _main(env) == EXIT_RUN_ERROR
    assert not (env / "out").exists()


def test_bad_arguments(env):
    assert main(["--set", "final"]) == EXIT_USAGE


def test_uncached_embeddings_stop_the_run(env):
    assert _main(env, config=ResolveConfig(top_k=3)) == EXIT_RUN_ERROR


def test_the_scoring_set_needs_frozen_settings(env):
    assert _main(env) == EXIT_OK
    scoring = ["--set", "scoring"]
    args = dict(candidates_dir=env / "extract", out_dir=env / "out", embed_cache=env / "embed",
                response_cache=env / "responses", open_model=FakeLabeller,
                read_chunks=_chunks, config=CONFIG)
    assert main(scoring, **args) == EXIT_CHECK_FAILED  # no frozen.json
    run_report = {"provenance": {"config_hash": CONFIG.config_hash},
                  "adjudication": {"prompt_version": "resolve/v1@00000000",
                                   "model": "claude-haiku-5-5", "effort": "high"}}
    (env / "out" / "run1.json").write_text(json.dumps(run_report), encoding="utf-8")
    prompts = {"label": load_label_prompt().prompt_version, "resolve": "resolve/v1@00000000",
               "label_model": "gpt-6-luna@high", "resolve_model": "claude-haiku-5-5@high"}
    write_frozen(env / "out" / FROZEN_NAME, Frozen("0" * 64, prompts), {})
    assert main(scoring, **args) == EXIT_CHECK_FAILED  # another config hash
    (env / "out" / FROZEN_NAME).unlink()
    swapped = {**prompts, "resolve_model": "claude-sonnet-5-5@high"}
    write_frozen(env / "out" / FROZEN_NAME, Frozen(CONFIG.config_hash, swapped), {})
    assert main(scoring, **args) == EXIT_CHECK_FAILED  # the run used another adjudicator
    (env / "out" / FROZEN_NAME).unlink()
    write_frozen(env / "out" / FROZEN_NAME, Frozen(CONFIG.config_hash, prompts), {})
    assert main(scoring, **args) == EXIT_OK
    scored = read_draw(draw_path(env / "out", "scoring"))
    tuning = read_draw(draw_path(env / "out", "tuning"))
    assert scored.populations == {Stratum.CANDIDATE: 0, Stratum.NEAR: 0, Stratum.REST: 12}
    assert not {p.keys for p in scored.pairs} & {p.keys for p in tuning.pairs}
    assert len(read_labels(labels_path(env / "out", "scoring"))) == 12
    provenance = json.loads(draw_path(env / "out", "scoring").read_text())["provenance"]
    assert provenance["frozen"]["config_hash"] == CONFIG.config_hash


def test_a_pair_is_never_labelled_twice(env):
    assert _main(env) == EXIT_OK
    path = labels_path(env / "out", "tuning")
    first = read_labels(path)[0]
    with pytest.raises(LabelFileError, match="already labelled"):
        append_labels(path, [first])
    assert len(read_labels(path)) == DRAWN


def test_a_malformed_labels_file_is_bad_input(env):
    assert _main(env) == EXIT_OK
    with labels_path(env / "out", "tuning").open("a", encoding="utf-8") as fh:
        fh.write('{"same": true}\n')
    assert _main(env) == EXIT_USAGE


def test_strata_are_recorded_on_each_label(env):
    assert _main(env) == EXIT_OK
    strata = {lb.stratum for lb in read_labels(labels_path(env / "out", "tuning"))}
    assert strata <= set(Stratum)
