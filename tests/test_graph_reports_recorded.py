"""Community reports on real claude-haiku-5-5 replies, offline (Step 12).

Four replies recorded once by scripts/capture_report_responses.py (see
tests/report_helpers.py). These tests pin the request each community sends
and that the citation check keeps only sentences citing the community's
evidence; they do not claim the reports are good.
"""
import json

import pytest

from graph.community import LOWER, TOP
from graph.report_prompt import PUBLISHED, load_prompt, output_schema
from graph.report_request import build_lower_request
from graph.report_writer import Planned, ReportFailure, ReportRecord, ReportWriter, plan_top
from retrieve.answer_model import AnswerModelError
from retrieve.citations import split_sentences
from tests.anthropic_fixtures import with_text
from tests.report_helpers import (
    LOWER_IDS,
    RECORDED_UNDER,
    TOP_ID,
    Replay,
    community,
    load_report,
    lower_input,
    reply_json,
)


@pytest.fixture(scope="module")
def prompt():
    return load_prompt()


def _planned(prompt, community_id):
    recorded = load_report(community_id)
    built = lower_input(recorded)
    return Planned(community(recorded), build_lower_request(prompt, built), built.evidence,
                   tuple(p.chunk_id for p in built.passages))


def _communities():
    return [community(load_report(cid)) for cid in (TOP_ID, *LOWER_IDS)]


def _write_lower(prompt, model):
    writer = ReportWriter(model, prompt, workers=1)
    return writer, writer.write_planned([_planned(prompt, cid) for cid in LOWER_IDS])


def test_the_replies_were_recorded_with_the_current_pinned_prompt(prompt):
    assert prompt.prompt_version == RECORDED_UNDER
    assert prompt.file_sha256 == PUBLISHED["v1"]
    for cid in (*LOWER_IDS, TOP_ID):
        assert load_report(cid)["prompt_version"] == RECORDED_UNDER


@pytest.mark.parametrize("community_id", LOWER_IDS)
def test_a_lower_request_is_the_recorded_one_on_haiku_with_structured_output(prompt,
                                                                            community_id):
    request = _planned(prompt, community_id).request
    assert request.cache_key() == load_report(community_id)["request_sha256"]
    params = request.params()
    assert params["model"] == "claude-haiku-5-5" and params["max_tokens"] == 16_000
    assert params["output_config"] == {
        "effort": "high", "format": {"type": "json_schema", "schema": output_schema()}}
    assert params["system"] == [{"type": "text", "text": prompt.system,
                                 "cache_control": {"type": "ephemeral"}}]
    assert not {"temperature", "top_p", "top_k", "tools"} & set(params)
    assert request.level == LOWER


@pytest.mark.parametrize("community_id", LOWER_IDS)
def test_a_recorded_lower_report_keeps_only_sentences_citing_its_evidence(prompt, community_id):
    plan = _planned(prompt, community_id)
    record = ReportWriter(Replay(), prompt).write(plan)
    assert isinstance(record, ReportRecord) and record.status == "written"
    assert record.report.kept_sentences > 0 and record.usage.output_tokens > 0
    for sentence in split_sentences(record.text):
        assert sentence.citations and set(sentence.citations) <= plan.evidence
    assert set(record.chunk_ids) <= plan.evidence
    assert record.inputs == tuple(load_report(community_id)["inputs"])


def test_uncited_and_out_of_community_sentences_are_dropped_and_counted(prompt):
    plan = _planned(prompt, LOWER_IDS[0])
    body = load_report(LOWER_IDS[0])["response"]
    reply = reply_json(body)
    outside = next(c for c in load_report(LOWER_IDS[1])["evidence"] if c not in plan.evidence)
    reply["summary"] += " This group matters a great deal."
    reply["findings"].append({"text": f"A claim borrowed from elsewhere [{outside}]."})
    model = Replay({LOWER_IDS[0]: with_text(body, json.dumps(reply))})
    record = ReportWriter(model, prompt).write(plan)
    assert record.report.dropped == 2
    assert record.report.dropped_by_reason == {"citation_not_retrieved": 1, "no_citation": 1}
    assert outside not in record.chunk_ids and "great deal" not in record.text


def test_a_top_request_reads_only_its_lower_reports_kept_text(prompt):
    _, lower = _write_lower(prompt, Replay())
    (plan,) = plan_top(_communities(), lower, prompt)
    assert isinstance(plan, Planned) and plan.request.level == TOP
    assert plan.request.cache_key() == load_report(TOP_ID)["request_sha256"]
    assert plan.evidence == {c for r in lower for c in r.chunk_ids}
    assert plan.inputs == LOWER_IDS


def test_a_top_report_may_not_cite_a_chunk_its_lower_reports_did_not_keep(prompt):
    writer, lower = _write_lower(prompt, Replay())
    kept = {c for r in lower for c in r.chunk_ids}
    unkept = next(c for cid in LOWER_IDS for c in load_report(cid)["evidence"] if c not in kept)
    body = load_report(TOP_ID)["response"]
    reply = reply_json(body)
    reply["findings"].append({"text": f"A member's chunk no lower report kept [{unkept}]."})
    (plan,) = plan_top(_communities(), lower, prompt)
    record = ReportWriter(Replay({TOP_ID: with_text(body, json.dumps(reply))}), prompt) \
        .write(plan)
    assert record.report.dropped_by_reason == {"citation_not_retrieved": 1}
    assert set(record.chunk_ids) <= kept


def test_a_failed_lower_report_fails_its_top_with_no_top_call(prompt):
    class Failing(Replay):
        def complete(self, request):
            if request.community_id == LOWER_IDS[1]:
                self.calls.append(request.community_id)
                raise AnswerModelError("down")
            return super().complete(request)

    model = Failing()
    writer, lower = _write_lower(prompt, model)
    assert isinstance(lower[1], ReportFailure) and lower[1].reason == "call_failed"
    (top,) = writer.write_planned(plan_top(_communities(), lower, prompt))
    assert isinstance(top, ReportFailure) and top.reason == "lower_report_failed"
    assert TOP_ID not in model.calls


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_a_cut_off_or_refused_reply_is_invalid(prompt, stop):
    body = {**load_report(LOWER_IDS[0])["response"], "stop_reason": stop}
    outcome = ReportWriter(Replay({LOWER_IDS[0]: body}), prompt).write(
        _planned(prompt, LOWER_IDS[0]))
    assert isinstance(outcome, ReportFailure) and outcome.reason == "invalid_reply"
    assert outcome.usage is not None
