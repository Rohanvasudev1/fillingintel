"""Record real extraction responses as tests/fixtures/anthropic/extract_v2_{name}.json.

Sends two chunks to the extractor with the current extract prompt (v2),
uncached: NVIDIA's FY2026 10-K segment note, which names both reportable
segments and the revenue-by-end-market table (0125), and the NVDA 10-Q's
revenue-by-market-platform note (0032). The chunks come from the gzipped
fixtures through the real parser and chunker, so no database is needed. Each
fixture holds the chunk ID, the prompt version and the response body exactly as
the API returned it. No credentials are written.

The four prompt v1 fixtures (extract_{name}.json, no version in the name) were
recorded by this script's Step 8 version (commit 5905b2c) and are kept as they are.

Needs ``ANTHROPIC_API_KEY`` and the network; costs two extraction calls.

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python scripts/capture_extract_responses.py
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(REPO / "vendor" / "tiktoken"))

from extract.filers import FilingInfo  # noqa: E402
from extract.prompt import load_prompt  # noqa: E402
from extract.request import ChunkInput, build_request  # noqa: E402
from ingest.chunker import chunk_filing  # noqa: E402
from ingest.models import FilingMeta  # noqa: E402
from ingest.parser import parse_filing  # noqa: E402
from retrieve.answer_model import AnswerModelError, AnthropicAnswerModel  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "anthropic"
NOTE = "real API response, recorded by scripts/capture_extract_responses.py"
TEN_K = FilingMeta(
    cik="1045810", accession_no="0001045810-26-000021", form_type="10-K",
    company_name="NVIDIA CORP", fiscal_period="FY2026", report_date=date(2026, 1, 26),
    filing_date=date(2026, 2, 26), primary_document="nvda-20260126.htm",
)
TEN_Q = FilingMeta(
    cik="1045810", accession_no="0001045810-26-000075", form_type="10-Q",
    company_name="NVIDIA CORP", fiscal_period="FY2027-Q2", report_date=date(2026, 7, 27),
    filing_date=date(2026, 8, 28), primary_document="nvda-20260727.htm",
)
# fixture name -> (filing, gzipped fixture, chunk ordinal)
CASES = {
    "extract_v2_segment_note": (TEN_K, "nvda_10k.html.gz", 125),
    "extract_v2_10q_market_platforms": (TEN_Q, "nvda_10q.html.gz", 32),
}


def _filing_info(meta: FilingMeta) -> FilingInfo:
    return FilingInfo(cik=meta.cik, accession_no=meta.accession_no,
                      company_name=meta.company_name, form_type=meta.form_type,
                      fiscal_period=meta.fiscal_period)


def _chunk(meta: FilingMeta, fixture: str, ordinal: int) -> ChunkInput:
    with gzip.open(REPO / "tests" / "fixtures" / fixture, "rt", encoding="utf-8",
                   errors="replace") as fh:
        filing = parse_filing(fh.read(), meta)
    chunk_id = f"{meta.accession_no}:{ordinal:04d}"
    [chunk] = [c for c in chunk_filing(filing) if c.chunk_id == chunk_id]
    return ChunkInput(chunk.chunk_id, chunk.section, filing.text[chunk.char_start:chunk.char_end])


def main() -> int:
    prompt = load_prompt()
    try:
        with AnthropicAnswerModel.from_env() as claude:
            for name, (meta, fixture, ordinal) in CASES.items():
                chunk = _chunk(meta, fixture, ordinal)
                response = claude.complete(build_request(prompt, _filing_info(meta), chunk))
                recorded = {
                    "recorded": NOTE,
                    "chunk_id": chunk.chunk_id,
                    "prompt_version": prompt.prompt_version,
                    "api_ms": response.api_ms,
                    "response": dict(response.body),
                }
                (OUT / f"{name}.json").write_text(json.dumps(recorded, indent=2) + "\n",
                                                  encoding="utf-8")
                usage = response.body.get("usage", {})
                print(f"{name}: stop {response.body.get('stop_reason')}, "
                      f"output tokens {usage.get('output_tokens')}")
    except (AnswerModelError, OSError) as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
