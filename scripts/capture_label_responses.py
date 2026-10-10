"""Record real labeller replies for four NVDA run 1 pairs into tests/fixtures/label/responses/.

The pairs are tests/resolve_label_cases.py's: Groq / Groq, Inc., GDPR / U.K.
GDPR, Blackwell / GB200 and Revenue / Total revenue (FY2026). Chunk text
comes from the gzipped NVDA FY2026 10-K fixture through the real parser and
chunker, as the tests read it, so the requests and their cache keys match.
Replies are written in the response cache's own layout, so the tests replay
them through `CachedAnswerModel` with no call. No credentials are written.

Needs ``OPENAI_API_KEY`` and the network, with ``OPENAI_BASE_URL`` unset;
costs four gpt-6-luna calls at effort high. A pair already recorded is not
sent again.

    uv run --env-file .env python scripts/capture_label_responses.py
"""
from __future__ import annotations

import gzip
import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(REPO / "vendor" / "tiktoken"))

from eval.judging.openai_backend import OpenAIResponsesModel  # noqa: E402
from ingest.chunker import chunk_filing  # noqa: E402
from ingest.models import FilingMeta  # noqa: E402
from ingest.parser import parse_filing  # noqa: E402
from resolve.labeller import LabelPromptError, label_pair, load_label_prompt  # noqa: E402
from retrieve.answer_model import AnswerModelError  # noqa: E402
from retrieve.response_cache import CachedAnswerModel  # noqa: E402
from tests.resolve_label_cases import CASES, RESPONSES, drawn  # noqa: E402

TEN_K = FilingMeta(
    cik="1045810", accession_no="0001045810-26-000021", form_type="10-K",
    company_name="NVIDIA CORP", fiscal_period="FY2026", report_date=date(2026, 1, 26),
    filing_date=date(2026, 2, 26), primary_document="nvda-20260126.htm",
)


def _chunk_texts() -> dict[str, str]:
    with gzip.open(REPO / "tests" / "fixtures" / "nvda_10k.html.gz", "rt", encoding="utf-8",
                   errors="replace") as fh:
        filing = parse_filing(fh.read(), TEN_K)
    return {c.chunk_id: filing.text[c.char_start:c.char_end] for c in chunk_filing(filing)}


def main() -> int:
    try:
        prompt = load_label_prompt()
        texts = _chunk_texts()
        with OpenAIResponsesModel.from_env() as luna:
            model = CachedAnswerModel(luna, REPO / RESPONSES)
            for name, (a, b) in CASES.items():
                outcome = label_pair(drawn(name), "tuning", a, b, texts.__getitem__, model,
                                     prompt)
                label = outcome.label
                shown = outcome.failure if label is None else \
                    f"same={label.same}, quotes verified={label.quotes_verified}"
                print(f"{name}: {shown}, output tokens {outcome.usage.output_tokens}, "
                      f"replayed={outcome.from_cache}")
    except (AnswerModelError, LabelPromptError, OSError) as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
