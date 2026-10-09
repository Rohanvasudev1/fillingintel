"""extract_filing() rules, one broken model reply per rule (Step 8).

The filing text is the real NVDA FY2026 10-K fixture; the replies are
hand-written. Each broken reply breaks one rule in an otherwise valid reply,
and the test checks the item is rejected with its own reason while the rest
of the chunk's output survives.
"""
import logging

import pytest

from extract.filers import FILER_CIKS
from extract.outcomes import CandidateKind, FlagReason, Reason
from extract.pipeline import extract_filing
from extract.prompt import load_prompt
from graph.batch import NodeRef, Rule, Violation
from retrieve.answer_model import AnswerModelError
from tests.extract_fakes import (
    NVDA_FILING,
    FakeExtractModel,
    body,
    node,
    triple,
)
from tests.graph_test_data import NVDA_10K

SUPPLY = f"{NVDA_10K}:0012"
SEGMENTS = f"{NVDA_10K}:0123"
TSMC_SPAN = ("We utilize foundries, such as Taiwan Semiconductor Manufacturing Company "
             "Limited, or TSMC")
SAMSUNG_SPAN = "Samsung Electronics Co., Ltd., or Samsung"
FOUNDRY_SPAN = ("We utilize foundries, such as Taiwan Semiconductor Manufacturing Company "
                "Limited, or TSMC, and Samsung Electronics Co., Ltd., or Samsung, to produce "
                "our semiconductor wafers")
TSMC_NAME = "Taiwan Semiconductor Manufacturing Company Limited"
TSMC = NodeRef.of("Organization", key="Organization:taiwan semiconductor manufacturing company "
                                      "limited")
SAMSUNG = NodeRef.of("Organization", key="Organization:samsung electronics co., ltd.")
NVIDIA = NodeRef.of("Company", cik="1045810")
COMPUTE_SPAN = "The Compute & Networking segment includes our Data Center accelerated computing"
REVENUE_ROW = "| Revenue | $ | 193,479 | $ | 22,459 | $ | 215,938 |"


def _tsmc(**change):
    return {**node("n1", "Organization", TSMC_NAME, TSMC_SPAN), **change}


def _tsmc_supplies(**change):
    return {**triple("n1", "SUPPLIES", "FILER", FOUNDRY_SPAN), **change}


def _supply_reply(tsmc=None, tsmc_supplies=None, extra_triples=()):
    return {
        "nodes": [tsmc or _tsmc(), node("n2", "Organization", "Samsung Electronics Co., Ltd.",
                                        SAMSUNG_SPAN)],
        "triples": [tsmc_supplies or _tsmc_supplies(),
                    triple("n2", "SUPPLIES", "FILER", FOUNDRY_SPAN), *extra_triples],
    }


def _run(nvda_chunks, replies):
    model = FakeExtractModel(replies)
    chunks = [nvda_chunks[c] for c in replies]
    return extract_filing(NVDA_FILING, chunks, model, load_prompt())


def _supply_run(nvda_chunks, **reply):
    return _run(nvda_chunks, {SUPPLY: _supply_reply(**reply)})


def _reasons(run):
    return [r.reason for r in run.rejections]


def _assert_samsung_survives(run):
    assert SAMSUNG in {n_ref(n) for n in run.nodes}
    assert ("SUPPLIES", SAMSUNG, NVIDIA) in {(e.type, e.start, e.end) for e in run.edges}
    assert run.violations == ()


def n_ref(record):
    return NodeRef.of(record.label, key=record.properties["key"])


# ── the valid reply ───────────────────────────────────────────────────────────

def test_a_valid_reply_becomes_checked_records(nvda_chunks):
    run = _supply_run(nvda_chunks)
    prompt_version = load_prompt().prompt_version
    assert run.rejections == () and run.failures == () and run.violations == ()
    assert {n_ref(n) for n in run.nodes} == {TSMC, SAMSUNG}
    tsmc = next(n for n in run.nodes if n_ref(n) == TSMC)
    assert tsmc.properties["name"] == TSMC_NAME
    [evidence] = tsmc.evidence
    assert (evidence.chunk_id, evidence.confidence, evidence.extract_prompt) == (
        SUPPLY, "stated", prompt_version)
    supplies = next(e for e in run.edges if e.start == TSMC)
    assert (supplies.type, supplies.end) == ("SUPPLIES", NVIDIA)
    assert supplies.properties["chunk_ids"] == (SUPPLY,)
    assert supplies.properties["confidences"] == ("stated",)
    assert supplies.properties["extract_prompts"] == (prompt_version,)
    assert run.complete


def test_stored_spans_are_the_chunks_own_text(nvda_chunks):
    run = _supply_run(nvda_chunks)
    samsung = next(n for n in run.nodes if n_ref(n) == SAMSUNG)
    assert samsung.evidence[0].span == "Samsung Electronics Co\\., Ltd\\., or Samsung"
    assert samsung.evidence[0].span in nvda_chunks[SUPPLY].text


def test_markdown_escapes_are_dropped_from_names_and_keys(nvda_chunks):
    escaped = node("n2", "Organization", "Samsung Electronics Co\\., Ltd\\.", SAMSUNG_SPAN)
    reply = _supply_reply()
    reply["nodes"][1] = escaped
    run = _run(nvda_chunks, {SUPPLY: reply})
    samsung = next(n for n in run.nodes if n.properties["key"] == SAMSUNG.key_dict()["key"])
    assert samsung.properties["name"] == "Samsung Electronics Co., Ltd."


def test_enum_values_and_filer_references_ignore_case(nvda_chunks):
    run = _supply_run(
        nvda_chunks,
        tsmc=_tsmc(label="organization", confidence="STATED"),
        tsmc_supplies=_tsmc_supplies(type="supplies", end="filer", confidence="Stated"),
    )
    assert run.rejections == ()
    assert ("SUPPLIES", TSMC, NVIDIA) in {(e.type, e.start, e.end) for e in run.edges}


@pytest.mark.parametrize(("reference", "expected"), [
    ("FILER", NodeRef.of("Company", cik="1045810")),
    ("NVDA", NodeRef.of("Company", cik="1045810")),
    ("AMD", NodeRef.of("Company", cik="2488")),
    ("INTC", NodeRef.of("Company", cik="50863")),
])
def test_filer_references_map_to_ciks(nvda_chunks, reference, expected):
    run = _supply_run(nvda_chunks, tsmc_supplies=_tsmc_supplies(end=reference))
    assert ("SUPPLIES", TSMC, expected) in {(e.type, e.start, e.end) for e in run.edges}
    assert run.violations == ()


def test_this_filing_maps_to_the_accession_number(nvda_chunks):
    reply = {
        "nodes": [node("n1", "Segment", "Compute & Networking", COMPUTE_SPAN),
                  node("n2", "MetricValue", "Revenue", REVENUE_ROW, value=193479,
                       unit="USD millions", period="FY2026")],
        "triples": [triple("FILER", "HAS_SEGMENT", "n1", COMPUTE_SPAN),
                    triple("THIS_FILING", "REPORTS", "n2", REVENUE_ROW),
                    triple("n2", "MEASURES", "n1", REVENUE_ROW)],
    }
    run = _run(nvda_chunks, {SEGMENTS: reply})
    assert run.rejections == () and run.violations == ()
    reports = next(e for e in run.edges if e.type == "REPORTS")
    assert reports.start == NodeRef.of("Filing", accession_no=NVDA_10K)
    metric = next(n for n in run.nodes if n.label == "MetricValue")
    assert metric.properties["key"] == (
        f"{NVDA_10K}:Segment:compute & networking:revenue:FY2026")
    assert metric.properties["value"] == 193479.0
    assert (metric.properties["concept"], metric.properties["unit"],
            metric.properties["period"]) == ("Revenue", "USD millions", "FY2026")


def test_filer_ciks_match_the_corpus():
    from ingest.corpus import CIKS
    assert dict(FILER_CIKS) == CIKS


# ── one broken rule each ──────────────────────────────────────────────────────

def _assert_rejected(run, reason, *also):
    assert _reasons(run) == [reason, *also]
    _assert_samsung_survives(run)


def test_unknown_label(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc=_tsmc(label="Supplier"))
    _assert_rejected(run, Reason.UNKNOWN_LABEL, Reason.ENDPOINT_NODE_REJECTED)
    assert TSMC not in {n_ref(n) for n in run.nodes}


def test_unknown_edge_type(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc_supplies=_tsmc_supplies(type="MANUFACTURES_FOR"))
    _assert_rejected(run, Reason.UNKNOWN_EDGE_TYPE)
    assert TSMC in {n_ref(n) for n in run.nodes}


def test_cross_filing_edge_type_is_not_extracted_from_a_chunk(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc_supplies=_tsmc_supplies(type="PERSISTS_AS"))
    _assert_rejected(run, Reason.UNKNOWN_EDGE_TYPE)


def test_forbidden_endpoint_pair(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc_supplies=_tsmc_supplies(end="THIS_FILING"))
    _assert_rejected(run, Reason.DISALLOWED_ENDPOINT)


def test_unknown_filer_reference(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc_supplies=_tsmc_supplies(end="NVIDIA"))
    _assert_rejected(run, Reason.UNKNOWN_FILER_REFERENCE)


def test_dangling_local_node_id(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc_supplies=_tsmc_supplies(start="n9"))
    _assert_rejected(run, Reason.DANGLING_LOCAL_ID)


def test_a_local_id_used_twice_is_rejected(nvda_chunks):
    twice = _supply_reply()
    twice["nodes"].append(node("n1", "Organization", "TSMC", TSMC_SPAN))
    run = _run(nvda_chunks, {SUPPLY: twice})
    _assert_rejected(run, Reason.DANGLING_LOCAL_ID, Reason.DANGLING_LOCAL_ID,
                     Reason.ENDPOINT_NODE_REJECTED)


def test_paraphrased_node_span(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc=_tsmc(evidence_span="We use foundries such as TSMC"))
    _assert_rejected(run, Reason.SPAN_NOT_FOUND, Reason.ENDPOINT_NODE_REJECTED)


def test_triple_span_with_only_case_changed(nvda_chunks):
    run = _supply_run(nvda_chunks,
                      tsmc_supplies=_tsmc_supplies(evidence_span=FOUNDRY_SPAN.lower()))
    _assert_rejected(run, Reason.SPAN_NOT_FOUND)


def test_structural_label_emitted(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc=_tsmc(label="Company"))
    _assert_rejected(run, Reason.STRUCTURAL_LABEL, Reason.ENDPOINT_NODE_REJECTED)


def test_structural_edge_type_emitted(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc_supplies=_tsmc_supplies(type="PART_OF"))
    _assert_rejected(run, Reason.STRUCTURAL_LABEL)


def test_missing_required_property(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc=_tsmc(name=None))
    _assert_rejected(run, Reason.MISSING_PROPERTY, Reason.ENDPOINT_NODE_REJECTED)


def test_missing_role(nvda_chunks):
    officers = f"{NVDA_10K}:0016"
    row = "| Jen-Hsun Huang | 63 | President and Chief Executive Officer |"
    reply = {
        "nodes": [node("n1", "Person", "Jen-Hsun Huang", row)],
        "triples": [triple("n1", "HOLDS_ROLE_AT", "FILER", row, role="President"),
                    triple("n1", "HOLDS_ROLE_AT", "FILER", row)],
    }
    run = _run(nvda_chunks, {officers: reply})
    assert _reasons(run) == [Reason.MISSING_PROPERTY]
    [edge] = run.edges
    assert edge.properties["role"] == "President"


def test_stake_given_as_text(nvda_chunks):
    run = _supply_run(nvda_chunks, extra_triples=[
        triple("n1", "OWNS", "n2", FOUNDRY_SPAN, stake="half")])
    _assert_rejected(run, Reason.WRONG_PROPERTY_TYPE)


def test_wrong_property_type(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc=_tsmc(name=42))
    _assert_rejected(run, Reason.WRONG_PROPERTY_TYPE, Reason.ENDPOINT_NODE_REJECTED)


def test_metric_value_given_as_text(nvda_chunks):
    reply = {
        "nodes": [node("n1", "Segment", "Compute & Networking", COMPUTE_SPAN),
                  node("n2", "MetricValue", "Revenue", REVENUE_ROW, value="193,479",
                       unit="USD millions", period="FY2026")],
        "triples": [triple("FILER", "HAS_SEGMENT", "n1", COMPUTE_SPAN),
                    triple("n2", "MEASURES", "n1", REVENUE_ROW)],
    }
    run = _run(nvda_chunks, {SEGMENTS: reply})
    assert _reasons(run) == [Reason.WRONG_PROPERTY_TYPE, Reason.ENDPOINT_NODE_REJECTED]
    assert [n.label for n in run.nodes] == ["Segment"]


def test_metric_value_without_a_subject(nvda_chunks):
    reply = {
        "nodes": [node("n2", "MetricValue", "Revenue", REVENUE_ROW, value=193479,
                       unit="USD millions", period="FY2026")],
        "triples": [triple("THIS_FILING", "REPORTS", "n2", REVENUE_ROW)],
    }
    run = _run(nvda_chunks, {SEGMENTS: reply})
    assert _reasons(run) == [Reason.MISSING_PROPERTY, Reason.ENDPOINT_NODE_REJECTED]


def test_metric_value_whose_subject_was_rejected(nvda_chunks):
    reply = {
        "nodes": [node("n1", "Segment", "Compute & Networking", "not in the chunk"),
                  node("n2", "MetricValue", "Revenue", REVENUE_ROW, value=193479,
                       unit="USD millions", period="FY2026")],
        "triples": [triple("n2", "MEASURES", "n1", REVENUE_ROW)],
    }
    run = _run(nvda_chunks, {SEGMENTS: reply})
    assert _reasons(run) == [Reason.SPAN_NOT_FOUND, Reason.ENDPOINT_NODE_REJECTED,
                             Reason.ENDPOINT_NODE_REJECTED]


def test_a_rejection_keeps_the_chunk_and_the_candidate(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc=_tsmc(label="Supplier"))
    rejection = run.rejections[0]
    assert rejection.chunk_id == SUPPLY
    assert rejection.kind is CandidateKind.NODE
    assert rejection.candidate["label"] == "Supplier"
    assert run.rejection_counts == {Reason.UNKNOWN_LABEL: 1, Reason.ENDPOINT_NODE_REJECTED: 1}


# ── flags ─────────────────────────────────────────────────────────────────────

def test_name_not_in_its_span_is_flagged_and_kept(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc=_tsmc(evidence_span="We utilize foundries"))
    assert run.rejections == ()
    assert [(f.reason, f.chunk_id) for f in run.flags] == [(FlagReason.NAME_NOT_IN_SPAN, SUPPLY)]
    assert TSMC in {n_ref(n) for n in run.nodes}


def test_uncertain_confidence_is_flagged_and_kept(nvda_chunks):
    run = _supply_run(nvda_chunks, tsmc_supplies=_tsmc_supplies(confidence="uncertain"))
    assert run.rejections == ()
    assert [f.reason for f in run.flags] == [FlagReason.UNCERTAIN]
    assert run.flag_counts == {FlagReason.UNCERTAIN: 1}


# ── per-chunk failures ────────────────────────────────────────────────────────

def _failure_run(nvda_chunks, bad):
    return _run(nvda_chunks, {SEGMENTS: bad, SUPPLY: _supply_reply()})


def test_invalid_json_is_a_chunk_failure_and_other_chunks_run(nvda_chunks):
    run = _failure_run(nvda_chunks, '{"nodes": [')
    assert [f.chunk_id for f in run.failures] == [SEGMENTS]
    assert "JSON" in run.failures[0].reason
    _assert_samsung_survives(run)
    assert not run.complete


def test_an_api_error_is_a_chunk_failure(nvda_chunks):
    run = _failure_run(nvda_chunks, AnswerModelError("Anthropic API call failed: (HTTP 529)"))
    assert [f.chunk_id for f in run.failures] == [SEGMENTS]
    assert "529" in run.failures[0].reason
    _assert_samsung_survives(run)


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_a_cut_off_or_refused_reply_is_a_chunk_failure(nvda_chunks, stop_reason):
    run = _failure_run(nvda_chunks, body({"nodes": [], "triples": []}, stop_reason))
    assert [f.chunk_id for f in run.failures] == [SEGMENTS]
    assert stop_reason in run.failures[0].reason


def test_a_reply_of_the_wrong_shape_is_a_chunk_failure(nvda_chunks):
    run = _failure_run(nvda_chunks, {"nodes": {}, "triples": []})
    assert [f.chunk_id for f in run.failures] == [SEGMENTS]


def test_usage_is_summed_over_chunks(nvda_chunks):
    run = _run(nvda_chunks, {SEGMENTS: {"nodes": [], "triples": []}, SUPPLY: _supply_reply()})
    one = body({})["usage"]
    assert run.usage.input_tokens == 2 * one["input_tokens"]
    assert run.usage.output_tokens == 2 * one["output_tokens"]


# ── inputs and the batch check ────────────────────────────────────────────────

def test_chunks_from_another_filing_are_refused(nvda_chunks):
    chunk = nvda_chunks[SUPPLY]
    other = type(chunk)("0000002488-26-000018:0001", chunk.section, chunk.text)
    with pytest.raises(ValueError, match="not a chunk of"):
        extract_filing(NVDA_FILING, [other], FakeExtractModel({}), load_prompt())


def test_a_chunk_listed_twice_is_refused(nvda_chunks):
    chunk = nvda_chunks[SUPPLY]
    with pytest.raises(ValueError, match="twice"):
        extract_filing(NVDA_FILING, [chunk, chunk], FakeExtractModel({}), load_prompt())


def test_a_batch_check_violation_is_reported_as_an_extractor_bug(nvda_chunks, monkeypatch,
                                                                caplog):
    bug = Violation("node 0", Rule.MISSING_PROPERTY, "name is required")
    monkeypatch.setattr("extract.pipeline.check_batch", lambda *args, **kwargs: (bug,))
    with caplog.at_level(logging.ERROR, logger="extract.pipeline"):
        run = _supply_run(nvda_chunks)
    assert run.violations == (bug,)
    assert not run.complete
    assert "extractor bug" in caplog.text
