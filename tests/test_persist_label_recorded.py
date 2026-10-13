"""The persistence labeller on real gpt-6-luna replies to five AMD pairs, offline (Step 10,
ticket 06).

Recorded once by scripts/capture_persist_label_responses.py with prompt
persist_label/v1; chunk text comes from the AMD 10-K and 10-Q fixtures. These
tests pin what the labeller answered and that the code turns each reply into
a verified label; they do not claim the answers are right.
"""
import pytest

from ingest.provenance import REPO_ROOT
from persist.labeller import label_pair, load_prompt
from retrieve.response_cache import CachedAnswerModel
from tests.persist_helpers import RECORDED_PAIRS
from tests.persist_label_cases import FILING_OF, RESPONSES, drawn

SAME = {"seasonal_period": True, "guarantees_leases": True, "litigation": True,
        "litigation_climate": False, "currency_seasonal": False}


class NoCalls:
    def complete(self, request):
        raise AssertionError("a recorded reply is missing; the request changed")


@pytest.fixture(scope="module")
def outcomes(amd_10k_filing, amd_10q_filing):
    from ingest.chunker import chunk_filing

    texts = {c.chunk_id: filing.text[c.char_start:c.char_end]
             for filing in (amd_10k_filing, amd_10q_filing) for c in chunk_filing(filing)}
    model = CachedAnswerModel(NoCalls(), REPO_ROOT / RESPONSES)
    return {name: label_pair(drawn(name), old, new, FILING_OF, texts.__getitem__, model,
                             load_prompt())
            for name, (new, old) in RECORDED_PAIRS.items()}


def test_every_recorded_reply_replays_from_the_cache(outcomes):
    assert all(o.from_cache and o.failure is None for o in outcomes.values())


def test_the_recorded_verdicts(outcomes):
    assert {name: o.label.same for name, o in outcomes.items()} == SAME


def test_every_recorded_quote_is_found_in_its_own_factor(outcomes):
    assert all(o.label.quotes_verified for o in outcomes.values())
