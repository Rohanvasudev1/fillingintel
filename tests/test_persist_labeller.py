"""The persistence labeller's request and reply handling, with hand-written replies (Step 10,
ticket 06). Factors, spans and chunk IDs are the AMD fixture filings', as in
tests/persist_helpers.py."""
import json

import pytest

from persist.labeller import build_request, label_pair, load_prompt
from resolve.labeller import LABEL_EFFORT, LABEL_MODEL, LabelPromptError
from resolve.sample import DrawnPair, Stratum
from retrieve.answer_model import ApiResponse
from tests.openai_fixtures import RECORDED, with_text
from tests.persist_helpers import (
    AMD_10K,
    AMD_10Q,
    FILINGS,
    GUARANTEES_10K,
    GUARANTEES_10Q,
    SEASONAL,
    earlier,
    later,
)

OLD = earlier(GUARANTEES_10K, 53)
NEW = later(GUARANTEES_10Q, 78)
OTHER = earlier(SEASONAL, 21)
PAIR = DrawnPair(OLD.key, NEW.key, "RiskFactor", Stratum.CANDIDATE)
FILING_OF = {f.accession_no: f for f in FILINGS}


@pytest.fixture(scope="module")
def chunk_text(amd_10k_filing, amd_10q_filing):
    from ingest.chunker import chunk_filing

    texts = {c.chunk_id: filing.text[c.char_start:c.char_end]
             for filing in (amd_10k_filing, amd_10q_filing) for c in chunk_filing(filing)}
    return texts.__getitem__


class FakeOpenAI:
    def __init__(self, body: dict):
        self._body = body
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ApiResponse(body=self._body, api_ms=1.0)


def _reply(**fields) -> dict:
    reply = {"reason": "B adds leases to A's guarantees.",
             "quote_a": GUARANTEES_10K, "chunk_id_a": f"{AMD_10K}:0053",
             "quote_b": GUARANTEES_10Q, "chunk_id_b": f"{AMD_10Q}:0078", "same": True}
    return with_text(RECORDED, json.dumps({**reply, **fields}))


def test_the_prompt_file_is_pinned(tmp_path):
    (tmp_path / "v1.md").write_text("$earlier $later edited\n", encoding="utf-8")
    with pytest.raises(LabelPromptError, match="changed"):
        load_prompt("v1", prompts_dir=tmp_path)


def test_the_prompt_version_names_its_own_family():
    assert load_prompt().prompt_version.startswith("persist_label/v1@")


def test_the_request_shows_each_factor_with_its_filing_and_chunks(chunk_text):
    request = build_request(load_prompt(), OLD, NEW, FILING_OF, chunk_text)
    text = request.prompt
    assert (request.model, request.effort) == (LABEL_MODEL, LABEL_EFFORT)
    assert text.index(f"Title: {GUARANTEES_10K}") < text.index(f"Title: {GUARANTEES_10Q}")
    assert f"Filing: 10-K {AMD_10K}, period ending 2025-12-27" in text
    assert f"Filing: 10-Q {AMD_10Q}, period ending 2026-06-27" in text
    assert chunk_text(f"{AMD_10K}:0053") in text and chunk_text(f"{AMD_10Q}:0078") in text


def test_a_reply_becomes_a_label_with_the_earlier_factor_as_a(chunk_text):
    outcome = label_pair(PAIR, OLD, NEW, FILING_OF, chunk_text, FakeOpenAI(_reply()),
                         load_prompt())
    label = outcome.label
    assert outcome.failure is None
    assert (label.key_a, label.key_b, label.same, label.quotes_verified) == \
        (OLD.key, NEW.key, True, True)
    assert (label.set_name, label.stratum) == ("scoring", Stratum.CANDIDATE)
    assert label.prompt_version == load_prompt().prompt_version


def test_a_quote_from_the_other_factor_is_kept_unverified(chunk_text):
    reply = _reply(quote_a=GUARANTEES_10Q, chunk_id_a=f"{AMD_10Q}:0078")
    label = label_pair(PAIR, OLD, NEW, FILING_OF, chunk_text, FakeOpenAI(reply),
                       load_prompt()).label
    assert label.quotes_verified is False


def test_factors_that_are_not_the_pair_are_refused(chunk_text):
    with pytest.raises(ValueError, match="not the pair"):
        label_pair(PAIR, OTHER, NEW, FILING_OF, chunk_text, FakeOpenAI(_reply()), load_prompt())
