"""Record real community report replies as tests/fixtures/anthropic/report_{community_id}.json.

Takes top community CAPTURE_TOP from the latest communities run file and
writes its reportable lower communities' reports, then its own from theirs,
with the current report prompt, uncached. The inputs come from the real
builder output and Postgres, as `python -m graph.reports` reads them. Each
fixture holds the community, what its report was written from (a lower
community's members, edges, passages and evidence; a top community's lower
communities), the prompt version, the request's SHA-256 and the response body
exactly as the API returned it. No credentials are written.

Prints the estimate and sends nothing without --send. Needs
``ANTHROPIC_API_KEY``, ``DATABASE_URL`` and the network; its calls are not
counted in Step 12's spend, so the BUILD-LOG records them.

    env -u ANTHROPIC_BASE_URL uv run --env-file .env python scripts/capture_report_responses.py
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(REPO / "vendor" / "tiktoken"))

from graph.community_file import (  # noqa: E402
    latest_communities_file,
    read_communities_file,
)
from graph.report_estimate import estimate_reports  # noqa: E402
from graph.report_input import CommunityIndex  # noqa: E402
from graph.report_prompt import load_prompt  # noqa: E402
from graph.report_writer import (  # noqa: E402
    Planned,
    ReportRecord,
    ReportWriter,
    plan_lower,
    plan_top,
)
from graph.reports import read_batch_and_texts  # noqa: E402
from graph.step12_spend import COMMUNITIES_DIR  # noqa: E402
from retrieve.answer_model import AnswerModelError, AnthropicAnswerModel  # noqa: E402

CAPTURE_TOP = "top-36ff66bed99f"  # 43 members; three reportable lower communities
OUT = REPO / "tests" / "fixtures" / "anthropic"
NOTE = "real API response, recorded by scripts/capture_report_responses.py"


class _Recording:
    """Sends each request through *model* and keeps the response by community."""

    def __init__(self, model: AnthropicAnswerModel):
        self._model = model
        self.responses: dict[str, object] = {}

    def complete(self, request):
        response = self._model.complete(request)
        self.responses[request.community_id] = response
        return response


def _community_json(community) -> dict[str, object]:
    return {"community_id": community.community_id, "level": community.level,
            "members": list(community.members), "member_hash": community.member_hash,
            "parent": community.parent, "reportable": community.reportable}


def main(argv: list[str]) -> int:
    if os.environ.get("ANTHROPIC_BASE_URL"):
        print("ANTHROPIC_BASE_URL is set; unset it so filing text and the API key go only to "
              "Anthropic", file=sys.stderr)
        return 1
    prompt = load_prompt()
    communities = read_communities_file(latest_communities_file(COMMUNITIES_DIR)).communities
    chosen = [c for c in communities
              if c.community_id == CAPTURE_TOP or c.parent == CAPTURE_TOP]
    batch, texts = read_batch_and_texts()
    index = CommunityIndex.from_batch(batch)
    lower = plan_lower(chosen, index, texts, prompt)
    estimate = estimate_reports([p.request for p in lower], [len(lower)], prompt, workers=1)
    print(f"{len(lower)} lower reports and 1 top report; estimate ${estimate.usd:.3f}")
    if "--send" not in argv:
        print("Estimate only; rerun with --send to record the replies.")
        return 0
    inputs = {p.community.community_id: p for p in lower}
    try:
        with AnthropicAnswerModel.from_env() as claude:
            recording = _Recording(claude)
            writer = ReportWriter(recording, prompt, workers=1)
            lower_out = writer.write_planned(lower)
            top_plans = plan_top(chosen, lower_out, prompt)
            top_out = writer.write_planned(top_plans)
    except AnswerModelError as exc:
        print(f"capture failed: {exc!r}", file=sys.stderr)
        return 1
    plans = {p.community.community_id: p for p in [*lower, *top_plans] if isinstance(p, Planned)}
    for outcome in (*lower_out, *top_out):
        if not isinstance(outcome, ReportRecord):
            print(f"{outcome.community_id}: {outcome.reason}: {outcome.detail}", file=sys.stderr)
            return 1
        plan = plans[outcome.community_id]
        response = recording.responses[outcome.community_id]
        recorded = {"recorded": NOTE, "prompt_version": prompt.prompt_version,
                    "community": _community_json(plan.community),
                    "request_sha256": plan.request.cache_key(),
                    "evidence": sorted(plan.evidence), "inputs": list(plan.inputs),
                    "api_ms": response.api_ms, "response": dict(response.body)}
        if outcome.community_id in inputs:
            built = _lower_input(plan, index, texts)
            recorded["lower_input"] = built
        (OUT / f"report_{outcome.community_id}.json").write_text(
            json.dumps(recorded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report = outcome.report
        print(f"{outcome.community_id}: kept {report.kept_sentences}, dropped {report.dropped}, "
              f"output tokens {outcome.usage.output_tokens}, ${outcome.cost_usd:.4f}; "
              f"{report.title}")
    return 0


def _lower_input(plan: Planned, index: CommunityIndex, texts) -> dict[str, object]:
    from graph.report_input import lower_input

    return asdict(lower_input(plan.community, index, texts)) | {"evidence": sorted(plan.evidence)}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
