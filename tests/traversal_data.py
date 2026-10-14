"""The graph_traversal test setting, shared by its tests and the capture script
(Step 11 ticket 06).

The tests load NVIDIA's FY2026 10-K and FY2027-Q2 10-Q and AMD's FY2025 10-K, so
the arm's prompt lists these three filings; the capture script renders the same
prompt.  The questions are invented to hit ``tests/graph_local_data.py``'s graph
and are not eval questions.  Recorded replies live in
``tests/fixtures/anthropic/cypher_{name}.json``.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from retrieve.question_filter import CorpusFiling

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"
FILINGS = (
    CorpusFiling("0000002488-26-000018", "AMD", "10-K", "FY2025", date(2025, 12, 27)),
    CorpusFiling("0001045810-26-000021", "NVDA", "10-K", "FY2026", date(2026, 1, 26)),
    CorpusFiling("0001045810-26-000075", "NVDA", "10-Q", "FY2027-Q2", date(2026, 7, 27)),
)
QUESTIONS = {
    "supplier": "Which foundry does NVIDIA's fiscal 2026 10-K say manufactures its products?",
    "platform": "Which products does NVIDIA's Data Center market platform offer, according to "
                "its fiscal 2026 10-K?",
    "persistence": "How did NVIDIA's export-control risk factor change between its fiscal 2026 "
                   "10-K and its 10-Q for the second quarter of fiscal 2027?",
}


def recorded(name: str) -> dict:
    """One recorded reply: the question, the prompt version and the response body."""
    return json.loads((FIXTURES / f"cypher_{name}.json").read_text(encoding="utf-8"))
