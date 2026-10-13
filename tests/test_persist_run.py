"""`python -m persist.run`: guards, estimate and cap, outputs, reruns and exit codes (Step 10).

Candidates files are written in extract.run's line format around the AMD
fixture risk factors (tests/persist_helpers). Voyage and Claude are fakes
behind the real disk caches; chunk text comes from the AMD fixture filings.
"""
import hashlib
import json

import pytest

from ingest.voyage import BatchEmbedding
from persist.config import PersistConfig
from persist.links import EXACT_TITLE
from persist.paths import read_links
from persist.run import EXIT_CHECK_FAILED, EXIT_OK, EXIT_RUN_ERROR, EXIT_USAGE, main
from retrieve.answer_model import AnswerModelError, ApiResponse
from tests.extract_fakes import body
from tests.persist_helpers import (
    AMD_10K,
    AMD_10Q,
    CLIMATE,
    CURRENCY,
    FILINGS,
    GUARANTEES_10K,
    GUARANTEES_10Q,
    LITIGATION_10Q,
    SEASONAL,
    earlier,
    later,
)

FACTORS = (earlier(SEASONAL, 21), earlier(GUARANTEES_10K, 53), earlier(CURRENCY, 28),
           earlier(CLIMATE, 41), later(CURRENCY, 50), later(GUARANTEES_10Q, 78),
           later(LITIGATION_10Q, 71))
BY_KEY = {f.key: f for f in FACTORS}
EVERY_EARLIER = PersistConfig(top_k=4)  # hash vectors rank at random; send all four
ENV = {"ANTHROPIC_API_KEY": "test-key", "VOYAGE_API_KEY": "test-key",
       "DATABASE_URL": "postgresql://unused"}


def _line(factor):
    return {"kind": "node", "label": "RiskFactor", "item": f"RiskFactor(key={factor.key!r})",
            "properties": {"key": factor.key, "title": factor.name},
            "evidence": [{"chunk_id": e.chunk_id, "span": e.span, "confidence": "stated",
                          "extract_prompt": "extract/v2@00000000"} for e in factor.evidence]}


@pytest.fixture
def candidates(tmp_path):
    directory = tmp_path / "extract"
    directory.mkdir()
    for accession in (AMD_10K, AMD_10Q):
        lines = [_line(f) for f in FACTORS if f.key.startswith(accession)]
        (directory / f"{accession}-run1.jsonl").write_text(
            "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return directory


@pytest.fixture(scope="module")
def texts(amd_10k_filing, amd_10q_filing):
    from ingest.chunker import chunk_filing

    return {c.chunk_id: filing.text[c.char_start:c.char_end]
            for filing in (amd_10k_filing, amd_10q_filing) for c in chunk_filing(filing)}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


class FakeVoyage:
    """A vector per text from its hash; counts calls."""

    def __init__(self):
        self.calls = 0

    def embed_batch(self, texts, model, input_type):
        self.calls += 1
        vectors = [tuple((hashlib.sha256(t.encode()).digest()[i % 32] - 128) / 128
                         for i in range(1024)) for t in texts]
        return BatchEmbedding(vectors=tuple(vectors), total_tokens=5 * len(texts))


class FakeClaude:
    """Answers `same` for the guarantees pair, `distinct` otherwise, quoting each
    factor's first span."""

    def __init__(self, error=None):
        self.calls = 0
        self._error = error

    def complete(self, request):
        self.calls += 1
        if self._error is not None:
            raise self._error
        old, new = BY_KEY[request.earlier_key], BY_KEY[request.later_key]
        same = (new.name, old.name) == (GUARANTEES_10Q, GUARANTEES_10K)
        reply = {"answer": "same" if same else "distinct",
                 "quote_earlier": old.evidence[0].span, "chunk_id_earlier": old.chunk_ids[0],
                 "quote_later": new.evidence[0].span, "chunk_id_later": new.chunk_ids[0],
                 "reason": "fake"}
        return ApiResponse(body=body(reply), api_ms=1.0)


class _Context:
    def __init__(self, value):
        self._value = value

    def __enter__(self):
        return self._value

    def __exit__(self, *exc):
        return False


class Harness:
    def __init__(self, tmp_path, candidates, texts):
        self.out = tmp_path / "persistence"
        self.candidates = candidates
        self.embed_cache = tmp_path / "embed"
        self.response_cache = tmp_path / "responses"
        self.voyage = FakeVoyage()
        self.claude = FakeClaude()
        self.texts = texts
        self.opened_voyage = 0
        self.asked_chunks = []

    def run(self, *argv):
        def open_client():
            self.opened_voyage += 1
            return _Context(self.voyage)

        def read_texts(url, chunk_ids):
            self.asked_chunks += list(chunk_ids)
            return {c: self.texts[c] for c in chunk_ids}

        return main(list(argv), candidates_dir=self.candidates, out_dir=self.out,
                    embed_cache=self.embed_cache, response_cache=self.response_cache,
                    open_client=open_client, open_model=lambda: _Context(self.claude),
                    read_filings=lambda url: FILINGS, read_texts=read_texts,
                    config=EVERY_EARLIER)

    def report(self, n=1):
        return json.loads((self.out / f"run{n}.json").read_text(encoding="utf-8"))


@pytest.fixture
def harness(tmp_path, candidates, texts):
    return Harness(tmp_path, candidates, texts)


def test_a_run_writes_the_links_and_a_report(harness):
    assert harness.run() == EXIT_OK
    links = read_links(harness.out / "links.jsonl")
    assert sorted((BY_KEY[lk.later_key].name, BY_KEY[lk.earlier_key].name, lk.method == EXACT_TITLE)
                  for lk in links) == sorted([(CURRENCY, CURRENCY, True),
                                              (GUARANTEES_10Q, GUARANTEES_10K, False)])
    report = harness.report()
    assert report["provenance"]["status"] == "complete"
    assert report["provenance"]["prompt_version"].startswith("persist/v1@")
    assert report["provenance"]["model"] == "claude-haiku-5-5"
    assert report["provenance"]["links_sha256"] == hashlib.sha256(
        (harness.out / "links.jsonl").read_bytes()).hexdigest()
    assert report["counts"]["links_by_method"] == {
        EXACT_TITLE: 1, report["provenance"]["prompt_version"]: 1}
    assert report["counts"]["filing_pairs"] == 1
    assert report["counts"]["answers"] == {"distinct": 7, "same": 1}
    assert report["check_batch"] == {"violations": 0, "examples": []}
    assert len(report["decisions"]) == 8 and harness.claude.calls == 8
    assert report["adjudication"]["tokens_this_run"]["output"] > 0
    assert report["adjudication"]["cost_this_run_usd"] > 0


def test_a_rerun_replays_every_reply_and_embedding_from_the_caches(harness):
    assert harness.run() == EXIT_OK
    first = (harness.out / "links.jsonl").read_bytes()
    assert harness.run() == EXIT_OK
    assert harness.claude.calls == 8 and harness.opened_voyage == 1
    assert (harness.out / "links.jsonl").read_bytes() == first
    second = harness.report(2)
    assert second["adjudication"]["calls_made"] == 0
    assert second["adjudication"]["cost_this_run_usd"] == 0


def test_only_the_sent_chunks_are_read(harness):
    harness.run()
    exact_later = later(CURRENCY, 50).chunk_ids[0]
    assert set(harness.asked_chunks) == {f.chunk_ids[0] for f in FACTORS} - {exact_later}


def test_an_estimate_over_the_cap_makes_no_call_and_writes_nothing(harness, capsys):
    assert harness.run("--max-cost", "0") == EXIT_RUN_ERROR
    assert harness.claude.calls == 0
    assert not (harness.out / "links.jsonl").exists()
    err = capsys.readouterr()
    assert "estimate" in err.out and "--max-cost" in err.err


def test_the_cap_cannot_be_raised_above_two_dollars(harness):
    assert harness.run("--max-cost", "2.5") == EXIT_USAGE


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "DATABASE_URL"])
def test_a_missing_setting_stops_the_run(harness, monkeypatch, capsys, name):
    monkeypatch.delenv(name)
    assert harness.run() == EXIT_RUN_ERROR
    assert name in capsys.readouterr().err


def test_a_set_base_url_stops_the_run(harness, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy")
    assert harness.run() == EXIT_RUN_ERROR
    assert "ANTHROPIC_BASE_URL" in capsys.readouterr().err
    assert harness.claude.calls == 0


def test_a_failed_call_writes_an_incomplete_report_and_keeps_the_old_links(harness):
    assert harness.run() == EXIT_OK
    before = (harness.out / "links.jsonl").read_bytes()
    harness.claude = FakeClaude(error=AnswerModelError("overloaded"))
    harness.response_cache = harness.response_cache.parent / "empty"
    assert harness.run() == EXIT_RUN_ERROR
    assert harness.report(2)["provenance"]["status"] == "incomplete"
    assert (harness.out / "links.jsonl").read_bytes() == before


def test_no_candidates_files_is_bad_input(harness, tmp_path):
    harness.candidates = tmp_path / "nothing"
    assert harness.run() == EXIT_USAGE


def test_links_that_fail_the_write_paths_check_are_not_written(harness, monkeypatch):
    from graph.batch import Rule, Violation

    monkeypatch.setattr("persist.run.check_links",
                        lambda links: (Violation("edge 0", Rule.MISSING_EVIDENCE, "x"),))
    assert harness.run() == EXIT_CHECK_FAILED
    assert not (harness.out / "links.jsonl").exists()
    assert harness.report()["check_batch"]["violations"] == 1
