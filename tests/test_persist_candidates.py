"""Exact-title matches and Voyage neighbours for risk factors in one filing pair (Step 10)."""
import pytest

from persist.candidates import embed_text, exact_matches, neighbours, title_key
from tests.persist_helpers import (
    CLIMATE,
    CURRENCY,
    GUARANTEES_10K,
    GUARANTEES_10Q,
    SEASONAL,
    earlier,
    later,
)


def test_a_title_key_ignores_case_spacing_and_markdown_escapes():
    assert title_key("Our  operating results\\.") == title_key("our operating results.")


def test_a_trailing_period_still_makes_titles_differ():
    assert title_key(SEASONAL) != title_key(SEASONAL + ".")


def test_identical_normalized_titles_match():
    old = [earlier(SEASONAL, 21), earlier(CURRENCY, 28)]
    new = [later(SEASONAL.upper(), 49), later(GUARANTEES_10Q, 78)]
    assert exact_matches(new, old) == {new[0].key: old[0]}


def test_the_embedded_text_is_the_title_then_the_first_span_without_escapes():
    node = later("Title\\.", 49, span="The span\\.")
    assert embed_text(node) == "Title.: The span."


def test_each_later_factor_gets_its_top_k_earlier_neighbours_ranked_by_cosine():
    old = [earlier("e1", 1), earlier("e2", 2), earlier("e3", 3), earlier("e4", 4)]
    new = [later("n1", 5)]
    vectors = {old[0].key: (1.0, 0.0), old[1].key: (0.9, 0.1), old[2].key: (0.0, 1.0),
               old[3].key: (0.5, 0.5), new[0].key: (1.0, 0.0)}
    pairs = neighbours(new, old, vectors, top_k=3)
    assert [(p.earlier_key, p.rank) for p in pairs] == [
        (old[0].key, 1), (old[1].key, 2), (old[3].key, 3)]
    assert all(p.later_key == new[0].key for p in pairs)
    assert pairs[0].cosine == pytest.approx(1.0)


def test_equal_cosines_rank_by_key():
    old = [earlier("b", 1), earlier("a", 2)]
    new = [later("n", 5)]
    vectors = {old[0].key: (1.0, 0.0), old[1].key: (1.0, 0.0), new[0].key: (1.0, 0.0)}
    assert [p.earlier_key for p in neighbours(new, old, vectors, top_k=3)] == [
        old[1].key, old[0].key]


def test_fewer_earlier_factors_than_k_gives_them_all():
    old = [earlier(GUARANTEES_10K, 53)]
    new = [later(GUARANTEES_10Q, 78), later(CLIMATE, 41)]
    vectors = {old[0].key: (1.0, 0.0), new[0].key: (1.0, 0.0), new[1].key: (0.0, 1.0)}
    pairs = neighbours(new, old, vectors, top_k=3)
    assert [(p.later_key, p.rank) for p in pairs] == [(new[0].key, 1), (new[1].key, 1)]


def test_a_missing_vector_is_refused_with_its_key():
    old, new = [earlier("e", 1)], [later("n", 5)]
    with pytest.raises(ValueError, match="no vector"):
        neighbours(new, old, {old[0].key: (1.0,)}, top_k=3)
