"""The Streamlit app, driven by AppTest through a fake backend, offline (Step 14)."""
from __future__ import annotations

import re
from dataclasses import replace

from retrieve.answer import (
    DECLINE_TEXT,
    NOT_FOUND_TEXT,
    declined_without_call,
    not_found_without_call,
)
from retrieve.citations import enforce_citations
from tests.ui_apptest import TYPED, answer_html, ask_typed, ask_with, open_app
from tests.ui_fakes import CHUNK, FakeBackend, arm_result
from ui.benchmarks_view import RESULTS
from ui.committed import SampleMetrics
from ui.errors import NEO4J_DOWN, DatabaseUnavailable
from ui.samples import Sample
from ui.source import SourcePassage


def test_vector_is_the_default_option_with_its_note():
    at = open_app(FakeBackend())

    picker = at.selectbox(key="option")
    assert picker.value == "vector"
    assert "best measured arm" in picker.format_func("vector")
    assert "tuned on dev" in picker.format_func("router (dev-best)")
    assert len(picker.options) == 6
    assert not at.exception


LOOKUP = Sample("q0001", "lookup", "What did NVIDIA say about roses?")


def sample_backend(**kwargs) -> FakeBackend:
    return FakeBackend({(LOOKUP.question, "vector"): arm_result()},
                       samples={"vector": (LOOKUP,)}, chips=(LOOKUP,), **kwargs)


def test_a_sample_chip_answers_with_numbered_citations():
    backend = sample_backend()
    at = open_app(backend)

    at.pills(key="chip").set_value(LOOKUP.id).run()

    assert backend.asked == [(LOOKUP.question, "vector")]
    shown = answer_html(at)
    assert "A rose" in shown and "[1]" in shown
    assert CHUNK not in shown  # the marker replaces the bracketed chunk ID
    assert at.pills(key="citation").options == ["[1]"]
    assert at.pills(key="citation").value == CHUNK  # the first citation's passage opens
    assert not at.exception


PASSAGE = SourcePassage(
    chunk_id=CHUNK, accession_no="0001045810-26-000021", cik="0001045810", ticker="NVDA",
    company_name="NVIDIA CORP", form_type="10-K", fiscal_period="FY2026",
    section="part_ii_item_7", char_start=100, char_end=140, context_start=60,
    before="<script>alert(1)</script> | Revenue | ", chunk="**Data Center** rose $5 to $9",
    after=" | *note* [x](y)")


def test_a_citation_chip_shows_the_passage_escaped_inside_its_filing():
    backend = sample_backend(sources={CHUNK: PASSAGE})
    at = open_app(backend)
    at.pills(key="chip").set_value(LOOKUP.id).run()

    at.pills(key="citation").set_value(CHUNK).run()

    passage = next(h.value for h in at.html if "fi-passage" in h.value)
    assert "<script>" not in passage
    assert "&lt;script&gt;alert(1)&lt;/script&gt; | Revenue | " in passage
    assert "<mark" in passage and "**Data Center** rose $5 to $9</mark>" in passage
    assert " | *note* [x](y)" in passage
    header = " ".join(m.value for m in at.markdown)
    for shown in ("NVDA", "10-K", "FY2026", "part_ii_item_7", CHUNK):
        assert shown in header
    assert at.link_button[0].proto.url == PASSAGE.edgar_url
    assert backend.asked == [(LOOKUP.question, "vector")]  # the click kept the answer
    assert "A rose" in answer_html(at)


def test_a_live_question_shows_its_estimate_first_then_adds_its_cost_to_the_session():
    backend = FakeBackend({(TYPED, "vector"): arm_result(answer_cost=0.02)}, estimate=0.031,
                          max_spend=1.0)
    at = open_app(backend)
    before = " ".join(c.value for c in at.caption)
    assert r"\$0.031" in before and r"\$0.00 of \$1.00" in before

    at = ask_typed(at)

    assert backend.asked == [(TYPED, "vector")]
    assert "A rose" in answer_html(at)
    captions = " ".join(c.value for c in at.caption)
    assert r"\$0.02 of \$1.00" in captions
    assert re.search(r"(?<!\\)\$", captions) is None  # no pair of prices read as a formula


def test_the_cap_refuses_a_live_question_in_plain_words():
    backend = FakeBackend({(TYPED, "vector"): arm_result()}, estimate=0.40, max_spend=0.30)
    at = ask_typed(open_app(backend))

    assert backend.asked == []
    assert r"would pass the \$0.30 cap" in at.error[0].value
    assert not any("fi-answer" in h.value for h in at.html)


def test_a_backend_error_is_shown_as_its_plain_message():
    error = DatabaseUnavailable(NEO4J_DOWN)
    at = ask_typed(open_app(FakeBackend(error=error)))

    # Markdown-escaped, so it shows exactly as NEO4J_DOWN reads.
    assert at.error[0].value == (r"Neo4j is not reachable at NEO4J\_URI. Start it with docker "
                                 r"compose up \-d, then try again.")
    assert not at.exception


def test_a_declined_answer_shows_the_fixed_decline_sentence():
    declined = replace(arm_result(), retrieved=(), answer=declined_without_call())
    at = ask_with(declined)

    assert DECLINE_TEXT in answer_html(at)
    shown = " ".join(m.value for m in at.markdown)
    assert "sentences kept" not in shown  # nothing was written, so nothing to count
    assert "No chunks were retrieved." in shown


def test_a_not_found_answer_shows_the_fixed_sentence_and_counts_dropped_sentences():
    text = f"Revenue rose [{CHUNK}]. Margins fell. Costs [0000000000-00-000000:0001]."
    check = enforce_citations(text, {CHUNK})
    answer = replace(not_found_without_call(), citation_check=check, raw_text=text)
    at = ask_with(replace(arm_result(), answer=answer))

    shown = answer_html(at)
    assert NOT_FOUND_TEXT in shown and "Revenue rose" in shown
    assert "Margins fell" not in shown
    assert "2 sentences dropped" in " ".join(m.value for m in at.markdown)


def test_a_sample_shows_its_committed_dev_metrics_with_their_labels():
    metrics = SampleMetrics(
        run_file="2026-10-05-vector-dev-96e8c69.json", class_="lookup",
        retrieval={"recall@5": 1.0, "recall@10": 1.0, "precision@10": 0.1},
        wrong_evidence=False, judged={"faithfulness": 0.875}, judged_label="uncalibrated")
    at = open_app(sample_backend(metrics={("vector", LOOKUP.id): metrics}))
    at.pills(key="chip").set_value(LOOKUP.id).run()

    shown = " ".join(m.value for m in [*at.markdown, *at.caption])
    assert "agent-drafted questions, judged uncalibrated" in shown
    assert "2026-10-05-vector-dev-96e8c69.json" in shown
    table = next(d.value for d in at.dataframe if "metric" in d.value.columns)
    table = table.set_index("metric")["value"]
    assert table["recall@10"] == "1.000" and table["faithfulness (uncalibrated)"] == "0.875"
    assert table["wrong evidence"] == "no"


def test_every_answer_shows_its_gold_free_numbers_per_stage():
    at = ask_with(arm_result(embed_cost=0.0001, answer_cost=0.02))

    shown = " ".join(m.value for m in at.markdown)
    assert "Citations inside the retrieved chunks: 1 of 1" in shown
    stages = at.dataframe[-1].value.set_index("stage")
    assert stages.loc["generation", "USD"] == "0.0200"
    assert stages.loc["total", "ms"] == "1,200"
    assert stages.loc["total", "USD"] == "0.0201"
    assert "agent-drafted" not in " ".join(c.value for c in at.caption)  # not a sample


def test_the_benchmarks_tab_shows_the_committed_results_under_their_labels():
    at = open_app(FakeBackend())

    tab = at.tabs[1]
    assert tab.label == "Benchmarks"
    header = " ".join(e.value for e in [*tab.markdown, *tab.caption])
    for label in ("agent-drafted", "uncalibrated", "tuning data", "test"):
        assert label in header
    committed = RESULTS.read_text(encoding="utf-8")
    body = next(m.value for m in tab.markdown if m.value.startswith("# FilingIntel benchmark"))
    assert body.replace(r"\$", "$") == committed.strip()  # st.markdown strips the end
    assert r"\$0.0289" in body and re.search(r"(?<!\\)\$", body) is None  # never LaTeX


PRICEY = Sample("q0009", "decline", "Should I move $50 from *AMD* to $NVDA?")


def test_question_text_is_escaped_where_streamlit_reads_markdown():
    backend = FakeBackend({(PRICEY.question, "vector"): arm_result()},
                          samples={"vector": (PRICEY,)}, chips=(PRICEY,))
    at = open_app(backend)
    assert at.pills(key="chip").options == [r"decline: Should I move \$50 from \*AMD\* to \$NVDA?"]

    at.pills(key="chip").set_value(PRICEY.id).run()

    assert at.subheader[0].value == r"Should I move \$50 from \*AMD\* to \$NVDA?"


def test_a_refused_answer_says_so():
    refused = replace(not_found_without_call(), status="refused", refusal_category="cyber")
    at = ask_with(replace(arm_result(), answer=refused))

    assert "refused" in at.warning[0].value


def test_only_chips_the_chosen_option_replays_are_offered():
    other = Sample("q0002", "local", "What did Intel agree with the DOC?")
    backend = FakeBackend(samples={"vector": (LOOKUP, other), "graph_traversal": (other,)},
                          chips=(LOOKUP, other))
    at = open_app(backend)
    assert len(at.pills(key="chip").options) == 2

    at.selectbox(key="option").set_value("graph_traversal").run()

    assert at.pills(key="chip").options == ["local: What did Intel agree with the DOC?"]
    assert "1 sample chip is not cached for graph_traversal" in " ".join(
        c.value for c in at.caption)
