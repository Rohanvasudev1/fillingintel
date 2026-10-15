"""The citation check on a community report (Step 12, invariant 3)."""
import pytest

from graph.report_check import ReplyError, ReportReply, check_report, parse_report_reply
from tests.graph_test_data import CUSTOMERS_CHUNK, EXPORT_CHUNK, SUPPLY_CHUNK

EVIDENCE = frozenset({SUPPLY_CHUNK, EXPORT_CHUNK})


def test_cited_sentences_inside_the_evidence_are_kept():
    reply = ReportReply(
        "Foundry supply",
        f"NVIDIA relies on TSMC [{SUPPLY_CHUNK}]. Export rules apply [{EXPORT_CHUNK}].",
        (f"Supply is concentrated [{SUPPLY_CHUNK}].",),
    )
    checked = check_report(reply, EVIDENCE)
    assert checked.dropped == 0
    assert checked.summary == reply.summary
    assert checked.findings == reply.findings
    assert checked.report_chunk_ids == tuple(sorted(EVIDENCE))
    assert checked.kept_sentences == 3


def test_uncited_and_out_of_community_sentences_are_dropped_and_counted():
    reply = ReportReply(
        "Foundry supply",
        f"NVIDIA relies on TSMC [{SUPPLY_CHUNK}]. This is a key theme. "
        f"One customer is large [{CUSTOMERS_CHUNK}].",
        (f"Mixed [{SUPPLY_CHUNK}] and [{CUSTOMERS_CHUNK}].", "No citation here.",
         f"Export rules apply [{EXPORT_CHUNK}]."),
    )
    checked = check_report(reply, EVIDENCE)
    assert checked.summary == f"NVIDIA relies on TSMC [{SUPPLY_CHUNK}]."
    assert checked.findings == (f"Export rules apply [{EXPORT_CHUNK}].",)
    assert checked.dropped == 4
    assert checked.dropped_by_reason == {"no_citation": 2, "citation_not_retrieved": 2}
    assert CUSTOMERS_CHUNK not in checked.report_chunk_ids


def test_the_report_text_is_the_summary_then_one_line_per_finding():
    reply = ReportReply("T", f"A [{SUPPLY_CHUNK}].", (f"B [{EXPORT_CHUNK}].", "C."))
    assert check_report(reply, EVIDENCE).text == (
        f"A [{SUPPLY_CHUNK}].\n- B [{EXPORT_CHUNK}].")
    assert check_report(ReportReply("T", "None.", ("C.",)), EVIDENCE).text == ""


def test_a_reply_parses_from_the_schemas_json():
    text = '{"title": "T", "summary": "S", "findings": [{"text": "F1"}, {"text": "F2"}]}'
    assert parse_report_reply(text) == ReportReply("T", "S", ("F1", "F2"))


@pytest.mark.parametrize("text", [
    "not json", "[]", '{"title": "T", "summary": "S"}',
    '{"title": "T", "summary": "S", "findings": [{"text": 3}]}',
    '{"title": "T", "summary": 1, "findings": []}',
    '{"title": "T", "summary": "S", "findings": [{"text": "F", "x": 1}]}',
])
def test_a_reply_off_the_schema_is_an_error(text):
    with pytest.raises(ReplyError):
        parse_report_reply(text)
