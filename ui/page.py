"""The FilingIntel page: the Ask and Benchmarks tabs (Step 14).

``main`` draws the page with the ``Backend`` it is given; ``ui/app.py`` passes
the live one and the tests a fake.
"""
from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from ui.answer_view import first_citation, show_answer
from ui.backend import Backend
from ui.benchmarks_view import show_benchmarks
from ui.errors import BackendError
from ui.metrics_view import show_metrics
from ui.options import DEFAULT_OPTION, OPTIONS, OPTIONS_BY_LABEL
from ui.render import markdown_dollars, md_escape
from ui.samples import Sample
from ui.trace_view import show_trace

TITLE = "FilingIntel"
QUESTION_BOX_PX = 80


def main(backend: Backend) -> None:
    st.set_page_config(page_title=TITLE, layout="wide")
    st.title(TITLE)
    ask, benchmarks = st.tabs(["Ask", "Benchmarks"])
    with ask:
        _ask_view(backend)
    with benchmarks:
        show_benchmarks()


def _ask_view(backend: Backend) -> None:
    state = st.session_state
    state.setdefault("spent", 0.0)
    option = _picker()
    _sample_chips(backend, option)
    _sample_list(backend, option)
    _question_form()
    cost_line = st.empty()
    pending = state.pop("pending", None)
    if pending:
        _ask(backend, pending, option)
    cost_line.caption(markdown_dollars(_cost_line(backend, option)))
    if state.get("error"):
        st.error(md_escape(state["error"]))
    answer = state.get("answer")
    if answer is not None:
        show_answer(backend, answer)
        show_trace(answer)
        show_metrics(backend, answer)


def _queue(question: str) -> None:
    st.session_state["pending"] = question


def _queue_sample(key: str, samples: Sequence[Sample]) -> None:
    """A sample widget's callback: queue the picked sample and clear the widget."""
    picked = st.session_state.get(key)
    st.session_state[key] = None
    question = next((s.question for s in samples if s.id == picked), None)
    if question is not None:
        _queue(question)


def _sample_label(sample: Sample) -> str:
    """A pill label: pills render Markdown, so the question is escaped."""
    return f"{sample.class_}: {md_escape(sample.question)}"


def _sample_chips(backend: Backend, option: str) -> None:
    """The six chips, less any *option* would answer live: a chip never spends."""
    cached = {s.id for s in backend.samples(option)}
    chips = tuple(c for c in backend.chips() if c.id in cached)
    by_id = {s.id: s for s in chips}
    st.pills("Try a sample question", list(by_id), key="chip",
             format_func=lambda sid: _sample_label(by_id[sid]),
             on_change=_queue_sample, args=("chip", chips))
    hidden = len(backend.chips()) - len(chips)
    if hidden:
        noun = "chip is" if hidden == 1 else "chips are"
        st.caption(f"{hidden} sample {noun} not cached for {option}, so not offered here.")


def _sample_list(backend: Backend, option: str) -> None:
    samples = backend.samples(option)
    by_id = {s.id: s for s in samples}
    # Selectbox options are plain text, not Markdown, so the question needs no escaping.
    st.selectbox(f"Or pick any of the {len(samples)} dev questions {option} answers from the "
                 "cache", list(by_id), index=None, key="sample_pick",
                 placeholder="Choose a sample question",
                 format_func=lambda sid: f"{by_id[sid].class_}: {by_id[sid].question}",
                 on_change=_queue_sample, args=("sample_pick", samples))


def _question_form() -> None:
    with st.form("ask_form", border=False):
        typed = st.text_area("Or type your own question", key="typed",
                             height=QUESTION_BOX_PX)
        if st.form_submit_button("Ask") and typed.strip():
            _queue(typed.strip())


def _cost_line(backend: Backend, option: str) -> str:
    """The live estimate for *option* and what the session has spent, shown before sending."""
    try:
        estimate = f"about ${backend.estimate(option):.3f}"
    except BackendError as exc:
        estimate = f"an unknown amount ({md_escape(str(exc))})"
    return (f"A typed question that is not a sample runs live and costs {estimate} with "
            f"{option} (its mean dev cost per question). Session spend: "
            f"${st.session_state['spent']:.2f} of ${backend.max_spend:.2f}. "
            "Samples replay for free.")


def _ask(backend: Backend, question: str, option: str) -> None:
    state = st.session_state
    with st.status(f"Answering with {option}...", expanded=False) as status:
        try:
            answer = backend.answer(question, option, spent_usd=state["spent"])
        except BackendError as exc:
            status.update(label="Not answered", state="error")
            state["error"] = str(exc)
            return
        status.update(label=f"Answered with {option}", state="complete")
    state["spent"] = state["spent"] + answer.spent_usd
    state["answer"] = answer
    state["error"] = None
    state["citation"] = first_citation(answer)


def _option_label(label: str) -> str:
    note = OPTIONS_BY_LABEL[label].note
    return f"{label} ({note})" if note else label


def _picker() -> str:
    labels = [o.label for o in OPTIONS]
    return st.selectbox("Answer with", labels, index=labels.index(DEFAULT_OPTION),
                        format_func=_option_label, key="option")
