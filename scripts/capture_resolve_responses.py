"""Record real adjudicator replies as tests/fixtures/anthropic/resolve_{name}.json.

Sends ten pairs of nodes from NVDA run 1 to claude-sonnet-5-5 with the current
resolve prompt, uncached: the Groq agreements, Huawei, GDPR against its long
name and against U.K. GDPR, OBBBA, Teter, Blackwell / GB200, GeForce / GeForce
NOW, and Revenue / Total revenue for FY2026. The nodes come from the run 1
candidates file; their chunk text comes from the gzipped NVDA 10-K fixture
through the real parser and chunker, so no database is needed. Each fixture
holds both nodes, the prompt version and the response body exactly as the API
returned it. No credentials are written.

Needs ``ANTHROPIC_API_KEY`` and the network; costs ten adjudicator calls.

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python \\
        scripts/capture_resolve_responses.py data/extract/0001045810-26-000021-run1.jsonl
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
from resolve.nodes import ResolveNode, load_nodes  # noqa: E402
from resolve.prompt import load_prompt  # noqa: E402
from resolve.request import build_request  # noqa: E402
from retrieve.answer_model import AnswerModelError, AnthropicAnswerModel  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "anthropic"
NOTE = "real API response, recorded by scripts/capture_resolve_responses.py"
TEN_K = FilingMeta(
    cik="1045810", accession_no="0001045810-26-000021", form_type="10-K",
    company_name="NVIDIA CORP", fiscal_period="FY2026", report_date=date(2026, 1, 26),
    filing_date=date(2026, 2, 26), primary_document="nvda-20260126.htm",
)
_REVENUE = "0001045810-26-000021:1045810:{}:FY2026"
# fixture name -> the two node keys, as run 1 keyed them
PAIRS = {
    "groq_license_agreements": ("Agreement:license agreement with groq",
                                "Agreement:non‐exclusive license agreement with groq"),
    "groq_ip_arrangement": ("Agreement:intellectual property license arrangement with groq",
                            "Agreement:license agreement with groq"),
    "huawei": ("Organization:huawei", "Organization:huawei technologies co. ltd."),
    "gdpr_long_name": ("Regulation:gdpr", "Regulation:general data protection regulation"),
    "gdpr_uk": ("Regulation:gdpr", "Regulation:u.k. gdpr"),
    "obbba": ("Regulation:obbba", "Regulation:one big beautiful bill act"),
    "teter": ("Person:mr. teter", "Person:timothy s. teter"),
    "blackwell_gb200": ("Product:blackwell", "Product:gb200"),
    "geforce_now": ("Product:geforce", "Product:geforce now"),
    "revenue": (_REVENUE.format("revenue"), _REVENUE.format("total revenue")),
}


def node_json(node: ResolveNode) -> dict[str, object]:
    return {"key": node.key, "label": node.label, "name": node.name,
            "evidence": [asdict(e) for e in node.evidence],
            "metric": None if node.metric is None else asdict(node.metric)}


def _texts() -> dict[str, str]:
    with gzip.open(REPO / "tests" / "fixtures" / "nvda_10k.html.gz", "rt", encoding="utf-8",
                   errors="replace") as fh:
        filing = parse_filing(fh.read(), TEN_K)
    return {c.chunk_id: filing.text[c.char_start:c.char_end] for c in chunk_filing(filing)}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    nodes, _ = load_nodes([Path(argv[0])])
    by_key = {n.key: n for n in nodes}
    prompt, texts = load_prompt(), _texts()
    try:
        with AnthropicAnswerModel.from_env() as claude:
            for name, keys in PAIRS.items():
                a, b = (by_key[k] for k in sorted(keys))
                response = claude.complete(build_request(prompt, a, b, texts))
                recorded = {"recorded": NOTE, "prompt_version": prompt.prompt_version,
                            "node_a": node_json(a), "node_b": node_json(b),
                            "api_ms": response.api_ms, "response": dict(response.body)}
                (OUT / f"resolve_{name}.json").write_text(
                    json.dumps(recorded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                text = "".join(c.get("text", "") for c in response.body.get("content", []))
                print(f"{name}: stop {response.body.get('stop_reason')}, "
                      f"output tokens {response.body.get('usage', {}).get('output_tokens')}, "
                      f"{text[:160]}")
    except (AnswerModelError, OSError, KeyError) as exc:
        print(f"capture failed: {exc!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
