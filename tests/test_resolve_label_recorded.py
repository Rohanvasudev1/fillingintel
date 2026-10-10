"""Real gpt-6-luna labeller replies for four NVDA run 1 pairs, replayed offline (Step 9).

Recorded once by scripts/capture_label_responses.py. The request is rebuilt
from the fixture filing's chunk text, so a replay also checks that the
request (prompt, schema, model, effort) has not changed since the recording.
"""
import pytest

from ingest.provenance import REPO_ROOT
from resolve.labeller import label_pair, load_label_prompt
from retrieve.response_cache import CachedAnswerModel
from tests.resolve_label_cases import CASES, RESPONSES, drawn

# The labeller's verdicts, which agree with CONTEXT.md's same-entity rule.
EXPECTED = {"groq": True, "gdpr": False, "blackwell_gb200": False, "revenue": True}


class _NoCalls:
    def complete(self, request):
        raise AssertionError("a recorded reply is missing; the request changed")


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_recorded_reply_replays_as_a_label(name, test_graph_chunks):
    a, b = CASES[name]
    model = CachedAnswerModel(_NoCalls(), REPO_ROOT / RESPONSES)
    outcome = label_pair(drawn(name), "tuning", a, b, lambda c: test_graph_chunks[c][1],
                         model, load_label_prompt())
    assert outcome.from_cache and outcome.failure is None
    assert outcome.label.same is EXPECTED[name]
    assert outcome.label.quotes_verified
