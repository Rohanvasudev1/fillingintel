"""Record real persistence adjudicator replies as tests/fixtures/anthropic/persist_{name}.json.

Sends five pairs of AMD risk factors to the adjudicator model with the current
persist prompt, uncached. The later factor is a bold heading of the AMD
FY2026-Q2 10-Q fixture, whose most recent 10-K is the AMD FY2025 10-K fixture;
the earlier factor is that 10-K's risk factor as AMD run 1 titled it. The pairs
are the same title with and without a final period, a commitments factor
expanded with "leases", the litigation factor against its 10-K title, and two
pairs with different risks. The chunk text comes from the gzipped fixtures
through the real parser and chunker, so no database is needed. Each fixture
holds both factors, the prompt version and the response body exactly as the
API returned it. No credentials are written.

Needs ``ANTHROPIC_API_KEY`` and the network; costs five adjudicator calls
(well under $0.01).

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python scripts/capture_persist_responses.py
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(REPO / "vendor" / "tiktoken"))

from ingest.chunker import chunk_filing  # noqa: E402
from ingest.models import FilingMeta  # noqa: E402
from ingest.parser import parse_filing  # noqa: E402
from persist.prompt import load_prompt  # noqa: E402
from persist.request import build_request  # noqa: E402
from resolve.nodes import ResolveNode  # noqa: E402
from retrieve.answer_model import AnswerModelError, AnthropicAnswerModel  # noqa: E402
from tests.persist_helpers import RECORDED_PAIRS  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "anthropic"
NOTE = "real API response, recorded by scripts/capture_persist_responses.py"
FILINGS = {
    "amd_10k.html.gz": FilingMeta(
        cik="2488", accession_no="0000002488-26-000018", form_type="10-K",
        company_name="ADVANCED MICRO DEVICES INC", fiscal_period="FY2025",
        report_date=date(2025, 12, 27), filing_date=date(2026, 2, 25),
        primary_document="amd-20251227.htm"),
    "amd_10q.html.gz": FilingMeta(
        cik="2488", accession_no="0000002488-26-000123", form_type="10-Q",
        company_name="ADVANCED MICRO DEVICES INC", fiscal_period="FY2026-Q2",
        report_date=date(2026, 6, 27), filing_date=date(2026, 8, 7),
        primary_document="amd-20260627.htm"),
}


def factor_json(factor: ResolveNode) -> dict[str, object]:
    return {"key": factor.key, "label": factor.label, "name": factor.name,
            "evidence": [asdict(e) for e in factor.evidence]}


def _texts() -> dict[str, str]:
    texts = {}
    for name, meta in FILINGS.items():
        with gzip.open(REPO / "tests" / "fixtures" / name, "rt", encoding="utf-8",
                       errors="replace") as fh:
            filing = parse_filing(fh.read(), meta)
        texts |= {c.chunk_id: filing.text[c.char_start:c.char_end] for c in chunk_filing(filing)}
    return texts


def main() -> int:
    prompt, texts = load_prompt(), _texts()
    try:
        with AnthropicAnswerModel.from_env() as claude:
            for name, (later, earlier) in RECORDED_PAIRS.items():
                response = claude.complete(build_request(prompt, later, earlier, texts))
                recorded = {"recorded": NOTE, "prompt_version": prompt.prompt_version,
                            "later": factor_json(later), "earlier": factor_json(earlier),
                            "api_ms": response.api_ms, "response": dict(response.body)}
                (OUT / f"persist_{name}.json").write_text(
                    json.dumps(recorded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                text = "".join(c.get("text", "") for c in response.body.get("content", []))
                print(f"{name}: stop {response.body.get('stop_reason')}, "
                      f"output tokens {response.body.get('usage', {}).get('output_tokens')}, "
                      f"{text[:200]}")
    except (AnswerModelError, OSError) as exc:
        print(f"capture failed: {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
