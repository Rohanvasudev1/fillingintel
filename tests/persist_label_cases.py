"""The five AMD pairs whose real persistence-labeller replies are recorded (Step 10, ticket 06).

They are tests/persist_helpers.RECORDED_PAIRS, whose adjudicator replies are
recorded too. scripts/capture_persist_label_responses.py sends them once;
tests/test_persist_label_recorded.py replays the replies offline.
"""
from resolve.sample import DrawnPair, Stratum
from tests.persist_helpers import FILINGS, RECORDED_PAIRS

RESPONSES = "tests/fixtures/label/responses"
FILING_OF = {f.accession_no: f for f in FILINGS}


def drawn(name: str) -> DrawnPair:
    """The drawn pair for RECORDED_PAIRS[*name*], earlier factor first."""
    new, old = RECORDED_PAIRS[name]
    return DrawnPair(old.key, new.key, "RiskFactor", Stratum.CANDIDATE)
