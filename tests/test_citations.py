"""Citation enforcement: split an answer into sentences and keep only those whose
every ``[chunk_id]`` was retrieved (Step 5 ticket 05, invariant 3)."""
from retrieve.citations import enforce_citations, split_sentences

A = "0001045810-26-000075:0042"
B = "0001045810-26-000075:0043"
C = "0000002488-25-000012:0107"  # well formed, but not retrieved below
RETRIEVED = {A, B}


def _texts(sentences):
    return [s.text for s in sentences]


def test_cited_sentences_are_kept_and_uncited_ones_dropped_and_counted():
    answer = f"Revenue rose 114% [{A}]. Demand was strong across regions. Margins fell [{B}]."
    result = enforce_citations(answer, RETRIEVED)
    assert _texts(result.kept) == [f"Revenue rose 114% [{A}].", f"Margins fell [{B}]."]
    assert [(d.text, d.reason) for d in result.dropped] == [
        ("Demand was strong across regions.", "no_citation")
    ]
    assert result.sentences == 3
    assert result.text == f"Revenue rose 114% [{A}]. Margins fell [{B}]."


def test_a_sentence_citing_any_chunk_that_was_not_retrieved_is_dropped():
    answer = f"Revenue rose [{A}]. Capex grew [{C}]. Supply was tight [{A}][{C}]."
    result = enforce_citations(answer, RETRIEVED)
    assert _texts(result.kept) == [f"Revenue rose [{A}]."]
    assert [(d.reason, d.citations) for d in result.dropped] == [
        ("citation_not_retrieved", (C,)),
        ("citation_not_retrieved", (A, C)),
    ]


def test_structural_counts_cover_every_citation_including_dropped_sentences():
    answer = f"Revenue rose [{A}][{B}]. Capex grew [{C}]. No source here."
    result = enforce_citations(answer, RETRIEVED)
    assert (result.citations, result.citations_retrieved) == (3, 2)


def test_a_citation_after_the_full_stop_belongs_to_the_sentence_before_it():
    answer = f"Revenue rose. [{A}] Margins fell [{B}]."
    assert [s.citations for s in split_sentences(answer)] == [(A,), (B,)]


def test_decimals_and_abbreviations_do_not_end_a_sentence():
    answer = (
        f"NVIDIA Corp. reported $26.0 billion of U.S. revenue, i.e. 46% of the total, "
        f"vs. $22.1 billion a year ago [{A}]. Intel Inc. disagreed [{B}]."
    )
    sentences = split_sentences(answer)
    assert len(sentences) == 2
    assert sentences[0].citations == (A,)


def test_several_citations_in_one_bracket_or_adjacent_brackets_are_all_read():
    answer = f"Revenue rose [{A}, {B}]. Margins fell [{A}; {B}]. Costs grew [{B}][{A}]."
    assert [s.citations for s in split_sentences(answer)] == [(A, B), (A, B), (B, A)]


def test_a_repeated_citation_counts_once_per_sentence():
    (sentence,) = split_sentences(f"Revenue rose [{A}] and margins fell [{A}].")
    assert sentence.citations == (A,)


def test_bracketed_text_that_is_not_a_chunk_id_is_not_a_citation():
    result = enforce_citations("Revenue rose [sic] sharply [NVDA 10-K].", RETRIEVED)
    assert result.dropped[0].reason == "no_citation"
    assert result.citations == 0


def test_each_line_is_split_on_its_own_and_blank_lines_are_ignored():
    answer = f"Revenue rose [{A}]\n\n- Margins fell [{B}]\n- No source here"
    result = enforce_citations(answer, RETRIEVED)
    assert _texts(result.kept) == [f"Revenue rose [{A}]", f"- Margins fell [{B}]"]
    assert _texts(result.dropped) == ["- No source here"]
    assert result.text == f"Revenue rose [{A}]\n- Margins fell [{B}]"


def test_a_line_holding_only_citations_attaches_to_the_sentence_before_it():
    (sentence,) = split_sentences(f"Revenue rose 114% year on year.\n[{A}]")
    assert sentence.citations == (A,)


def test_a_question_mark_or_exclamation_ends_a_sentence_too():
    answer = f"Why did revenue rise? [{A}] Data centre demand! [{B}]"
    assert [s.citations for s in split_sentences(answer)] == [(A,), (B,)]


def test_an_empty_answer_has_no_sentences():
    result = enforce_citations("  \n ", RETRIEVED)
    assert (result.kept, result.dropped, result.text, result.sentences) == ((), (), "", 0)
