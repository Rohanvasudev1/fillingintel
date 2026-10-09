"""Markdown escape handling on its own (Step 8)."""
from extract.spans import find_span, unescape


def test_an_escaped_backslash_keeps_one_backslash():
    assert unescape("a\\\\.b") == "a\\.b"
    assert unescape("Co\\., Ltd\\.") == "Co., Ltd."


def test_span_check_reads_an_escaped_backslash_the_same_way():
    text = "x a\\\\.b y"  # markdown for "x a\\.b y"
    assert find_span("a\\\\.b", text) == "a\\\\.b"
    assert find_span("a.b", text) is None
