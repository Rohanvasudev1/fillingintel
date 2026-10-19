"""The corruption test's four corruptors on real dev answers (Step 13 ticket 04).

Each corruptor makes exactly one change to one answer, is deterministic, and skips
(with a reason) when it cannot apply.
"""
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from eval.calibration.answers import RUNS_DIR, load_answers
from eval.calibration.corruptors import (
    ADDED_FACT_MARKER,
    CORPUS_FISCAL_YEARS,
    KINDS,
    NUMBER_FACTOR,
    Corruption,
    Skipped,
    corrupt,
)

CORPUS = Path(__file__).parent / "fixtures" / "corpus_filings.json"


@pytest.fixture(scope="module")
def answers():
    return [a for a in load_answers(RUNS_DIR) if a.kind == "answer" and a.kept]


def _by_kind(answer, texts=None):
    return {c.kind: c for c in corrupt(answer, texts or {})}


def _changed(original, corrupted):
    return [i for i, (a, b) in enumerate(zip(original.kept, corrupted.kept, strict=False))
            if a != b]


def test_corpus_years_match_the_corpus_manifest():
    filings = json.loads(CORPUS.read_text(encoding="utf-8"))
    years = sorted({int(f["fiscal_period"][2:6]) for f in filings})
    assert tuple(years) == CORPUS_FISCAL_YEARS


def test_every_answer_gets_all_four_kinds_in_order(answers):
    for answer in answers[:20]:
        assert tuple(c.kind for c in corrupt(answer, {})) == KINDS


def test_changed_number_scales_one_number_with_a_unit(answers):
    answer = next(a for a in answers
                  if a.kept[0].text.startswith("AMD's Data Center net revenue was $12.6 billion"))
    found = _by_kind(answer)["changed_number"]
    assert isinstance(found, Corruption)
    assert _changed(answer, found) == [found.sentence_index] == [0]
    assert found.before == "$12.6 billion"
    assert found.after == f"${12.6 * NUMBER_FACTOR:.1f} billion"
    sentence = found.kept[0]
    assert sentence.text == answer.kept[0].text.replace("$12.6 billion", found.after, 1)
    assert sentence.citations == answer.kept[0].citations


def test_changed_number_skips_an_answer_with_no_unit_number(answers):
    plain = next(a for a in answers
                 if not any(re.search(r"\d\s?(%|percent|billion|million)|\$\d", s.text)
                            for s in a.kept))
    found = _by_kind(plain)["changed_number"]
    assert isinstance(found, Skipped) and "no number" in found.reason


def test_changed_number_skips_a_value_a_retrieved_chunk_already_states(answers):
    answer = next(a for a in answers
                  if a.kept[0].text.startswith("AMD's Data Center net revenue was $12.6 billion"))
    scaled = f"${12.6 * NUMBER_FACTOR:.1f} billion"
    texts = {answer.retrieved[0]: f"revenue of {scaled[1:]}"}
    found = _by_kind(answer, texts)["changed_number"]
    assert not (isinstance(found, Corruption) and found.after == scaled)


def test_swapped_period_changes_only_the_year(answers):
    answer = next(a for a in answers if re.search(r"fiscal 20\d\d", a.kept[0].text))
    found = _by_kind(answer)["swapped_period"]
    assert isinstance(found, Corruption)
    assert _changed(answer, found) == [found.sentence_index]
    old_year, new_year = int(found.before[-4:]), int(found.after[-4:])
    assert old_year != new_year and new_year in CORPUS_FISCAL_YEARS
    assert found.before[:-4] == found.after[:-4], "only the year moves"


def test_wrong_citation_uses_the_lowest_ranked_uncited_chunk(answers):
    answer = answers[0]
    found = _by_kind(answer)["wrong_citation"]
    assert isinstance(found, Corruption)
    cited = {c for s in answer.kept for c in s.citations}
    expected = [c for c in answer.retrieved if c not in cited][-1]
    (index,) = _changed(answer, found)
    assert index == found.sentence_index
    assert len(answer.kept[index].citations) == 1
    assert found.kept[index].citations == (expected,)
    assert f"[{expected}]" in found.kept[index].text
    assert found.kept[index].text.replace(expected, "") == \
        answer.kept[index].text.replace(answer.kept[index].citations[0], "")


def test_wrong_citation_skips_when_every_retrieved_chunk_is_cited(answers):
    answer = answers[0]
    cited = tuple(dict.fromkeys(c for s in answer.kept for c in s.citations))
    found = _by_kind(replace(answer, retrieved=cited))["wrong_citation"]
    assert isinstance(found, Skipped) and "uncited" in found.reason


def test_added_sentence_is_appended_with_an_existing_citation(answers):
    answer = answers[0]
    found = _by_kind(answer)["added_sentence"]
    assert isinstance(found, Corruption)
    assert found.kept[:-1] == answer.kept and len(found.kept) == len(answer.kept) + 1
    assert found.sentence_index == len(answer.kept)
    added = found.kept[-1]
    assert ADDED_FACT_MARKER in added.text
    assert added.citations == (answer.kept[-1].citations[0],)


def test_added_sentence_skips_when_a_chunk_mentions_the_planted_fact(answers):
    answer = answers[0]
    texts = {answer.retrieved[3]: f"Our campus in {ADDED_FACT_MARKER} opened."}
    found = _by_kind(answer, texts)["added_sentence"]
    assert isinstance(found, Skipped) and ADDED_FACT_MARKER in found.reason


def test_corruption_is_deterministic(answers):
    for answer in answers[:10]:
        assert corrupt(answer, {}) == corrupt(answer, {})
