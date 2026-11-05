"""AppTest helpers for the Streamlit page's tests (Step 14)."""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

from tests.ui_fakes import FakeBackend

TYPED = "How did AMD describe export controls?"


def _page(backend):
    from ui.page import main

    main(backend)


def open_app(backend: FakeBackend) -> AppTest:
    at = AppTest.from_function(_page, kwargs={"backend": backend}, default_timeout=10)
    return at.run()


def ask_typed(at: AppTest, question: str = TYPED) -> AppTest:
    at.text_area(key="typed").input(question)
    return at.button(key="FormSubmitter:ask_form-Ask").click().run()


def ask_with(result, option: str = "vector", **kwargs) -> AppTest:
    """The page after *result* answered a typed question through *option*."""
    at = open_app(FakeBackend({(TYPED, option): result}, **kwargs))
    if option != "vector":
        at.selectbox(key="option").set_value(option).run()
    return ask_typed(at)


def answer_html(at: AppTest) -> str:
    return next(h.value for h in at.html if "fi-answer" in h.value)


def page_text(at: AppTest) -> str:
    """Every Markdown, caption and HTML string on the page, joined."""
    return " ".join(e.value for e in [*at.markdown, *at.caption, *at.html])
