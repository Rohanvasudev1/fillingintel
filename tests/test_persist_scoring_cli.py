"""`python -m persist.freeze`, `persist.label` and `persist.score` end to end (Step 10, ticket 06).

Each test first runs `python -m persist.run` on the AMD fixture factors, with
fake Voyage and Claude behind the real disk caches (tests/test_persist_run.py's
harness), so the freeze, draw, labels and score read a real run report, link
file and embedding cache. gpt-6-luna is a fake that calls a pair the same risk
when both titles are one of the two linked pairs.
"""
import json

import pytest

from persist import freeze, label, score
from persist.score_files import DRAW_NAME, FROZEN_NAME, LABELS_NAME
from retrieve.answer_model import ApiResponse
from tests.openai_fixtures import RECORDED, with_text
from tests.persist_helpers import CURRENCY, FILINGS, GUARANTEES_10K, GUARANTEES_10Q
from tests.test_persist_run import (  # noqa: F401 - fixtures
    ENV,
    EVERY_EARLIER,
    Harness,
    _Context,
    candidates,
    texts,
)

SAME = ((GUARANTEES_10K, GUARANTEES_10Q), (CURRENCY, CURRENCY))
CLEAN = "abc1234"


class FakeLuna:
    def __init__(self):
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        titles = [line.removeprefix("Title: ").removesuffix(".")
                  for line in request.prompt.splitlines() if line.startswith("Title: ")]
        same = tuple(titles) in {(a.removesuffix("."), b.removesuffix(".")) for a, b in SAME}
        reply = {"reason": "fake", "quote_a": "x", "chunk_id_a": "x", "quote_b": "y",
                 "chunk_id_b": "y", "same": same}
        return ApiResponse(body=with_text(RECORDED, json.dumps(reply)), api_ms=1.0)


class Chain:
    def __init__(self, harness, tmp_path):
        self.h = harness
        self.luna = FakeLuna()
        self.label_cache = tmp_path / "label-responses"

    @property
    def out(self):
        return self.h.out

    def freeze(self, commit=CLEAN):
        return freeze.main([], out_dir=self.out, commit=lambda: commit, config=EVERY_EARLIER)

    def label(self, *argv):
        deps = label.LabelDeps(
            out_dir=self.out, candidates_dir=self.h.candidates, embed_cache=self.h.embed_cache,
            response_cache=self.label_cache, open_model=lambda: _Context(self.luna),
            read_filings=lambda url: FILINGS, read_texts=self.read_texts,
            commit=lambda: CLEAN)
        return label.main(list(argv), deps, config=EVERY_EARLIER)

    def score(self):
        return score.main([], out_dir=self.out, commit=lambda: CLEAN, config=EVERY_EARLIER)

    def read_texts(self, url, chunk_ids):
        return {c: self.h.texts[c] for c in chunk_ids}

    def read(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def label_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


@pytest.fixture
def chain(tmp_path, candidates, texts):  # noqa: F811 - fixtures imported above
    harness = Harness(tmp_path, candidates, texts)
    assert harness.run() == 0
    return Chain(harness, tmp_path)


def test_freeze_label_and_score_end_to_end(chain):
    assert chain.freeze() == freeze.EXIT_OK
    frozen = chain.read(FROZEN_NAME)
    assert frozen["config_hash"] == EVERY_EARLIER.config_hash
    assert frozen["prompts"]["persist"].startswith("persist/v1@")
    assert frozen["prompts"]["label"].startswith("persist_label/v1@")
    assert frozen["provenance"]["run_report"] == "run1.json"

    assert chain.label() == label.EXIT_OK
    drawn = chain.read(DRAW_NAME)
    # 3 later x 4 earlier factors: the exact pair and 8 adjudicated, 3 rest, no near
    assert drawn["populations"] == {"candidate": 9, "near": 0, "rest": 3}
    assert len(drawn["pairs"]) == 12 and chain.luna.calls == 12
    run = chain.read("scoring-label-run1.json")
    assert run["counts"]["same"] == 2 and run["provenance"]["run_report"] == "run1.json"

    assert chain.score() == score.EXIT_OK
    scored = chain.read("scoring-score1.json")
    assert scored["provenance"]["note"] == "agent-labelled (gpt-6-luna)"
    assert scored["provenance"]["config_hash"] == EVERY_EARLIER.config_hash
    assert (scored["precision"]["value"], scored["recall"]["value"]) == (1.0, 1.0)
    # the adjudicator answered same for guarantees and distinct for the 7 others: all right
    assert (scored["adjudicator"]["judged"], scored["adjudicator"]["correct"]) == (8, 8)


def test_freeze_refuses_a_dirty_tree(chain):
    assert chain.freeze(commit=CLEAN + "+dirty") == freeze.EXIT_CHECK_FAILED
    assert not (chain.out / FROZEN_NAME).exists()


def test_freeze_refuses_a_link_file_the_run_did_not_write(chain):
    links = chain.out / "links.jsonl"
    links.write_text(links.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    assert chain.freeze() == freeze.EXIT_CHECK_FAILED


def test_freeze_happens_once(chain):
    assert chain.freeze() == freeze.EXIT_OK
    assert chain.freeze() == freeze.EXIT_CHECK_FAILED


def test_labelling_needs_the_freeze(chain):
    assert chain.label() == label.EXIT_CHECK_FAILED
    assert chain.luna.calls == 0 and not (chain.out / DRAW_NAME).exists()


def test_labelling_refuses_another_openai_base_url(chain, monkeypatch):
    chain.freeze()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")
    assert chain.label() == label.EXIT_RUN_ERROR
    assert chain.luna.calls == 0


def test_an_estimate_over_the_cap_makes_no_call(chain):
    chain.freeze()
    assert chain.label("--max-cost", "0") == label.EXIT_RUN_ERROR
    assert chain.luna.calls == 0 and not (chain.out / LABELS_NAME).exists()


def test_a_second_labelling_run_sends_nothing_and_keeps_the_draw(chain):
    chain.freeze()
    chain.label()
    first = (chain.out / DRAW_NAME).read_bytes()
    assert chain.label() == label.EXIT_OK
    assert chain.luna.calls == 12
    assert (chain.out / DRAW_NAME).read_bytes() == first


def test_links_changed_after_the_freeze_stop_the_score(chain):
    chain.freeze()
    chain.label()
    links = chain.out / "links.jsonl"
    links.write_text(links.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    assert chain.score() == score.EXIT_CHECK_FAILED


def test_the_links_are_scored_once(chain):
    chain.freeze()
    chain.label()
    assert chain.score() == score.EXIT_OK
    assert chain.score() == score.EXIT_CHECK_FAILED
    assert not (chain.out / "scoring-score2.json").exists()
