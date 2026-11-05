"""Write ui/samples.json: the dev questions each picker option answers from the cache.

Asks every ``dev`` question through each of the six picker options with dummy
API keys, so a cache miss fails instead of spending, and keeps the ones that
answer (``ui.samples.select_samples``).  ``test`` questions are never asked.
Needs the dev Postgres and Neo4j running, since a replay still retrieves; makes
no network call:

    uv run --env-file .env python scripts/build_ui_samples.py

It prints how many questions replay per option and why the others did not.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from contextlib import AbstractContextManager
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(REPO / "vendor" / "tiktoken"))

from eval.judging.openai_backend import API_KEY_ENV as OPENAI_KEY_ENV  # noqa: E402
from eval.question_sets import load_question_sets  # noqa: E402
from ingest.voyage import API_KEY_ENV as VOYAGE_KEY_ENV  # noqa: E402
from retrieve.answer_model import API_KEY_ENV as ANTHROPIC_KEY_ENV  # noqa: E402
from retrieve.arm import Arm  # noqa: E402
from retrieve.arms import ARMS  # noqa: E402
from retrieve.response_cache import DEFAULT_CACHE_DIR  # noqa: E402
from ui.options import OPTIONS, option_spec, picker_option  # noqa: E402
from ui.samples import (  # noqa: E402
    SAMPLE_SPLIT,
    SAMPLES_FILE,
    Samples,
    select_samples,
    write_samples,
)

DUMMY_KEYS = {ANTHROPIC_KEY_ENV: "dummy-ui-samples", VOYAGE_KEY_ENV: "dummy-ui-samples",
              OPENAI_KEY_ENV: "dummy-ui-samples"}


def open_option(label: str) -> AbstractContextManager[Arm]:
    return option_spec(picker_option(label), ARMS).open(DEFAULT_CACHE_DIR, None)


def _report(samples: Samples, asked: int) -> None:
    for label in samples.options:
        found, missed = samples.options[label], samples.skipped[label]
        print(f"{label}: {len(found)} of {asked} replay")
        for reason, count in Counter(s.reason for s in missed).most_common():
            print(f"    {count} not: {reason}")
    print("chips: " + ", ".join(f"{c.class_} {c.id}" for c in samples.chips))


def main() -> int:
    os.environ.update(DUMMY_KEYS)  # a cache miss fails with an auth error; nothing is spent
    os.environ.pop("ANTHROPIC_BASE_URL", None)
    records = [r for s in load_question_sets() for r in s.records if r.split == SAMPLE_SPLIT]
    samples = select_samples(open_option, records, [o.label for o in OPTIONS])
    write_samples(samples, SAMPLES_FILE)
    _report(samples, len(records))
    print(f"wrote {SAMPLES_FILE.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
