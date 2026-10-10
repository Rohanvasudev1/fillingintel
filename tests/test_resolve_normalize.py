"""Match keys and legal-suffix stripping for entity resolution (Step 9)."""
import pytest

from resolve.normalize import match_key


@pytest.mark.parametrize(("printed", "key"), [
    ("Groq, Inc.", "groq"),
    ("Groq, Inc", "groq"),
    ("Groq", "groq"),
    ("Huawei Technologies Co. Ltd.", "huawei technologies"),
    ("Huawei Technologies Co\\., Ltd\\.", "huawei technologies"),
    ("Huawei Technologies", "huawei technologies"),
    ("Taiwan Semiconductor Manufacturing Company Limited", "taiwan semiconductor manufacturing"),
    ("Computershare Trust Company, N.A.", "computershare trust"),
    ("Ernst & Young LLP", "ernst and young"),
    ("U.K. GDPR", "uk gdpr"),
    ("NVIDIA  Corporation", "nvidia"),
    ("ＮＶＩＤＩＡ", "nvidia"),
])
def test_match_key_strips_suffixes_punctuation_and_case(printed, key):
    assert match_key(printed) == key


@pytest.mark.parametrize(("printed", "key"), [
    ("Company", "company"),
    ("Inc.", "inc"),
    ("Co., Ltd.", "co"),
])
def test_stripping_never_empties_a_name(printed, key):
    assert match_key(printed) == key


def test_a_suffix_word_inside_a_name_is_kept():
    assert match_key("Inc Research Co") == "inc research"


def test_blank_name_has_an_empty_key():
    assert match_key("  ,. ") == ""
