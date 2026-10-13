"""Record real persistence-labeller replies for the five AMD pairs into tests/fixtures/label/.

The pairs are tests/persist_helpers.RECORDED_PAIRS, the same ones the
persistence adjudicator's replies were recorded for. Chunk text comes from the
gzipped AMD 10-K and 10-Q fixtures through the real parser and chunker, as the
tests read it, so the requests and their cache keys match. Replies are written
in the response cache's own layout (tests/fixtures/label/responses), so
tests/test_persist_label_recorded.py replays them with no call. No credentials
are written.

Needs ``OPENAI_API_KEY`` and the network, with ``OPENAI_BASE_URL`` unset;
costs five gpt-6-luna calls at effort high, estimated before any call. A pair
already recorded is not sent again.

    uv run --env-file .env python scripts/capture_persist_label_responses.py
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

from eval.judging.openai_backend import BASE_URL_ENV, OpenAIResponsesModel  # noqa: E402
from ingest.chunker import chunk_filing  # noqa: E402
from ingest.models import FilingMeta  # noqa: E402
from ingest.parser import parse_filing  # noqa: E402
from persist.labeller import build_request, label_pair, load_prompt  # noqa: E402
from resolve.label import estimate_usd  # noqa: E402
from resolve.labeller import LabelPromptError  # noqa: E402
from retrieve.answer_model import AnswerModelError  # noqa: E402
from retrieve.response_cache import CachedAnswerModel  # noqa: E402
from tests.persist_helpers import AMD_10K, AMD_10Q, RECORDED_PAIRS  # noqa: E402
from tests.persist_label_cases import FILING_OF, RESPONSES, drawn  # noqa: E402

METAS = (
    ("amd_10k.html.gz", FilingMeta(
        cik="2488", accession_no=AMD_10K, form_type="10-K",
        company_name="ADVANCED MICRO DEVICES INC",
        fiscal_period="FY2025", report_date=date(2025, 12, 27), filing_date=date(2026, 2, 25),
        primary_document="amd-20251227.htm")),
    ("amd_10q.html.gz", FilingMeta(
        cik="2488", accession_no=AMD_10Q, form_type="10-Q",
        company_name="ADVANCED MICRO DEVICES INC",
        fiscal_period="FY2026-Q2", report_date=date(2026, 6, 27), filing_date=date(2026, 8, 7),
        primary_document="amd-20260627.htm")),
)


def _chunk_texts() -> dict[str, str]:
    texts = {}
    for name, meta in METAS:
        with gzip.open(REPO / "tests" / "fixtures" / name, "rt", encoding="utf-8",
                       errors="replace") as fh:
            filing = parse_filing(fh.read(), meta)
        texts |= {c.chunk_id: filing.text[c.char_start:c.char_end] for c in chunk_filing(filing)}
    return texts


def main() -> int:
    if os.environ.get(BASE_URL_ENV):
        print(f"{BASE_URL_ENV} is set; unset it so filing text goes only to OpenAI",
              file=sys.stderr)
        return 1
    try:
        prompt = load_prompt()
        texts = _chunk_texts()
        cache_dir = REPO / RESPONSES
        probe = CachedAnswerModel(None, cache_dir)  # type: ignore[arg-type] - lookups only
        requests = [build_request(prompt, old, new, FILING_OF, texts.__getitem__)
                    for new, old in RECORDED_PAIRS.values()]
        uncached = [r for r in requests if not probe.contains(r)]
        print(f"{len(uncached)} of {len(requests)} pairs to send; estimate "
              f"${estimate_usd(uncached):.3f}")
        with OpenAIResponsesModel.from_env() as luna:
            model = CachedAnswerModel(luna, cache_dir)
            for name, (new, old) in RECORDED_PAIRS.items():
                outcome = label_pair(drawn(name), old, new, FILING_OF, texts.__getitem__, model,
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
