"""The answer, its citation chips and the source panel (Step 14)."""
from __future__ import annotations

import streamlit as st

from ui.backend import Backend, UiAnswer
from ui.errors import BackendError
from ui.render import (
    answer_html,
    citation_numbers,
    dropped_line,
    markdown_dollars,
    md_escape,
    passage_html,
)
from ui.source import CONTEXT_CHARS, SourcePassage


def first_citation(answer: UiAnswer) -> str | None:
    """The chunk the source panel opens on for a new answer: its first citation."""
    return next(iter(citation_numbers(answer.result.answer.citation_check.kept)), None)


def show_answer(backend: Backend, answer: UiAnswer) -> None:
    check = answer.result.answer.citation_check
    numbers = citation_numbers(check.kept)
    st.subheader(md_escape(answer.question))
    left, right = st.columns([3, 2], gap="large")
    with left:
        _status_note(answer)
        st.html(answer_html(answer.result.answer.status, check.kept, numbers))
        if check.sentences:
            st.markdown(dropped_line(check))
        if numbers:
            st.pills("Sources: choose one to read it in its filing", list(numbers),
                     key="citation", format_func=lambda cid: f"[{numbers[cid]}]")
    with right:
        chunk_id = st.session_state.get("citation")
        if chunk_id:
            _source_panel(backend, chunk_id, numbers.get(chunk_id))


def _status_note(answer: UiAnswer) -> None:
    written = answer.result.answer
    if written.status == "refused":
        category = f" ({md_escape(written.refusal_category)})" if written.refusal_category else ""
        st.warning(f"The answer model refused to answer{category}; nothing is shown.")
    elif written.status == "no_status":
        st.caption("The answer had no status line; its cited sentences are shown as written.")


def _source_panel(backend: Backend, chunk_id: str, number: int | None) -> None:
    sources = st.session_state.setdefault("sources", {})
    if chunk_id not in sources:
        try:
            sources[chunk_id] = backend.source(chunk_id)
        except BackendError as exc:
            st.error(markdown_dollars(str(exc)))
            return
    passage: SourcePassage = sources[chunk_id]
    marker = f"[{number}] " if number is not None else ""
    st.markdown(f"**{marker}{passage.ticker} {passage.form_type} {passage.fiscal_period}**  \n"
                f"{md_escape(passage.company_name)} · section `{passage.section}` · "
                f"chunk `{chunk_id}`")
    st.link_button("Open the filing on EDGAR", passage.edgar_url)
    st.caption(f"The cited chunk is highlighted inside up to {CONTEXT_CHARS:,} characters of "
               "the filing on each side, exactly as stored.")
    st.html(passage_html(passage))
