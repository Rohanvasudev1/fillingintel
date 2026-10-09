"""The evidence span check (Step 8), with quotes taken from the NVDA FY2026 10-K fixture.

A span matches its chunk after NFKC, curly quotes and dashes mapped to ASCII,
markdown escapes dropped and whitespace collapsed; the match is case-sensitive
and exact otherwise. A match returns the chunk's own text for the span.
"""
import pytest

from extract.spans import find_span
from tests.graph_test_data import NVDA_10K

COVER = f"{NVDA_10K}:0000"
PLATFORM = f"{NVDA_10K}:0005"
SOFTWARE = f"{NVDA_10K}:0009"
SUPPLY = f"{NVDA_10K}:0012"
SECURITY_RISK = f"{NVDA_10K}:0029"


@pytest.fixture(scope="module")
def texts(test_graph_chunks):
    return {chunk_id: text for chunk_id, (_, text) in test_graph_chunks.items()}


def _found(texts, chunk_id, span):
    found = find_span(span, texts[chunk_id])
    assert found is not None, span
    assert found in texts[chunk_id]  # the chunk's own text, never the model's
    return found


def test_straight_quotes_match_curly_quotes(texts):
    found = _found(texts, COVER, 'See the definitions of "large accelerated filer,"')
    assert "“large accelerated filer," in found


def test_hyphen_matches_em_dash(texts):
    found = _found(texts, SOFTWARE, "NVIDIA AI Enterprise-a comprehensive software suite")
    assert "Enterprise—a" in found


def test_hyphen_matches_en_dash(texts):
    found = _found(texts, PLATFORM, "a new processor class - the data processing unit")
    assert "class – the" in found


def test_line_breaks_match_a_space(texts):
    found = _found(texts, SECURITY_RISK, "business and reputation.** Security breaches")
    assert "reputation\\.**\n\nSecurity" in found


def test_markdown_escapes_are_ignored(texts):
    found = _found(texts, SUPPLY, "Samsung Electronics Co., Ltd., or Samsung")
    assert found == "Samsung Electronics Co\\., Ltd\\., or Samsung"


def test_span_with_the_escapes_kept_also_matches(texts):
    assert _found(texts, SUPPLY, "Co\\., Ltd\\.") == "Co\\., Ltd\\."


def test_surrounding_whitespace_is_trimmed(texts):
    assert _found(texts, SUPPLY, "  or TSMC,\n") == "or TSMC,"


def test_a_changed_word_does_not_match(texts):
    span = "We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Ltd"
    assert find_span(span, texts[SUPPLY]) is None


def test_changed_case_does_not_match(texts):
    assert find_span("we utilize foundries, such as Taiwan", texts[SUPPLY]) is None


def test_a_blank_span_does_not_match(texts):
    assert find_span("  \n", texts[SUPPLY]) is None
