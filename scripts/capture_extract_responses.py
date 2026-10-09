"""Record real extraction responses as tests/fixtures/anthropic/extract_{name}.json.

Sends four chunks of the NVDA FY2026 10-K fixture to the extractor with the
current extract prompt, uncached: a risk factor, the segment note, the
Business paragraph naming suppliers and a statement table. The chunks come
from the gzipped fixture through the real parser and chunker, so no database
is needed. Each fixture holds the chunk ID, the prompt version and the
response body exactly as the API returned it. No credentials are written.

Needs ``ANTHROPIC_API_KEY`` and the network; costs four extraction calls.

    uv run --env-file .env python scripts/capture_extract_responses.py
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
ACCESSION = "0001045810-26-000021"
CASES = {
    "extract_risk_factor": f"{ACCESSION}:0029",
    "extract_segment_note": f"{ACCESSION}:0123",
    "extract_suppliers": f"{ACCESSION}:0012",
    "extract_statement_table": f"{ACCESSION}:0089",
}
NOTE = "real API response, recorded by scripts/capture_extract_responses.py"
META = FilingMeta(
    cik="1045810", accession_no=ACCESSION, form_type="10-K", company_name="NVIDIA CORP",
    fiscal_period="FY2026", report_date=date(2026, 1, 26), filing_date=date(2026, 2, 26),
    primary_document="nvda-20260126.htm",
)
FILING = FilingInfo(cik=META.cik, accession_no=ACCESSION, company_name=META.company_name,
                    form_type=META.form_type, fiscal_period=META.fiscal_period)


def _chunks() -> dict[str, ChunkInput]:
    with gzip.open(REPO / "tests" / "fixtures" / "nvda_10k.html.gz", "rt", encoding="utf-8",
                   errors="replace") as fh:
        filing = parse_filing(fh.read(), META)
    return {c.chunk_id: ChunkInput(c.chunk_id, c.section, filing.text[c.char_start:c.char_end])
            for c in chunk_filing(filing)}


def main() -> int:
    prompt = load_prompt()
    chunks = _chunks()
    try:
        with AnthropicAnswerModel.from_env() as claude:
            for name, chunk_id in CASES.items():
                response = claude.complete(build_request(prompt, FILING, chunks[chunk_id]))
                fixture = {
                    "recorded": NOTE,
                    "chunk_id": chunk_id,
                    "prompt_version": prompt.prompt_version,
                    "api_ms": response.api_ms,
                    "response": dict(response.body),
                }
                (OUT / f"{name}.json").write_text(json.dumps(fixture, indent=2) + "\n",
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
