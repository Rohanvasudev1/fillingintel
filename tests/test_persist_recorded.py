"""The persistence adjudicator on real claude-haiku-5-5 replies to AMD pairs, offline (Step 10).

Five pairs recorded once by scripts/capture_persist_responses.py with prompt
persist/v1, each fixture holding both risk factors. The chunk text comes from
the AMD 10-K and 10-Q fixtures. These tests pin what the model answered and
that the code turns each answer into the right link or counted reason; they
do not claim the answers are right (ticket 06 scores that).
"""
import json

import pytest

from persist.adjudicate import ClaudePersistAdjudicator
from persist.candidates import CandidatePair
from persist.decisions import Answer
from persist.links import check_links, select_links
from persist.prompt import load_prompt
from retrieve.answer_model import AnswerModelError, ApiResponse
from tests.anthropic_fixtures import FIXTURES
from tests.persist_helpers import RECORDED_PAIRS

RECORDED_UNDER = "persist/v1@5eaa7a2c"
SAME = ("seasonal_period", "guarantees_leases", "litigation")
DISTINCT = ("litigation_climate", "currency_seasonal")


def _load(name):
    return json.loads((FIXTURES / f"persist_{name}.json").read_text(encoding="utf-8"))


class Replay:
    """Serves each recorded body for its pair; any other pair fails as an API error would."""

    def __init__(self):
        self._bodies = {}
        for name in RECORDED_PAIRS:
            recorded = _load(name)
            self._bodies[(recorded["later"]["key"], recorded["earlier"]["key"])] = \
                recorded["response"]

    def complete(self, request):
        body = self._bodies.get((request.later_key, request.earlier_key))
        if body is None:
            raise AnswerModelError("no recorded reply for this pair")
        return ApiResponse(body=body, api_ms=1.0, from_cache=True)


@pytest.fixture(scope="module")
def decisions(amd_10k_filing, amd_10q_filing):
    from ingest.chunker import chunk_filing

    texts = {c.chunk_id: filing.text[c.char_start:c.char_end]
             for filing in (amd_10k_filing, amd_10q_filing) for c in chunk_filing(filing)}
    adjudicator = ClaudePersistAdjudicator(Replay(), load_prompt(), texts)
    return {name: adjudicator.judge(CandidatePair(new.key, old.key, 1, 0.9), new, old)
            for name, (new, old) in RECORDED_PAIRS.items()}


def test_the_replies_were_recorded_with_the_current_prompt_and_factors():
    for name, (new, old) in RECORDED_PAIRS.items():
        recorded = _load(name)
        assert recorded["prompt_version"] == RECORDED_UNDER
        assert (recorded["later"]["key"], recorded["earlier"]["key"]) == (new.key, old.key)


@pytest.mark.parametrize("name", SAME)
def test_a_same_with_verified_quotes_links(decisions, name):
    decision = decisions[name]
    assert decision.answer is Answer.SAME and decision.links, decision.failure
    assert decision.usage is not None and decision.usage.output_tokens > 0


@pytest.mark.parametrize("name", DISTINCT)
def test_a_distinct_answer_does_not_link(decisions, name):
    assert decisions[name].answer is Answer.DISTINCT and decisions[name].failure == "distinct"


def test_the_recorded_links_pass_the_write_paths_check(decisions):
    links = select_links([], list(decisions.values()))
    assert len(links) == len(SAME)  # one later factor (litigation) has two pairs, one same
    assert check_links(links) == ()
    assert all(link.method == RECORDED_UNDER for link in links)
