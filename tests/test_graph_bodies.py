"""Risk factor body evidence in the graph builder (Step 11, ADR-0004 amendment).

Run on the real NVIDIA FY2026 10-K and FY2027 Q2 10-Q fixtures and their
chunks. The headings are the filings' own; the expected chunks were read from
the chunker's offsets, so a chunker change fails these tests.
"""
import pytest

from graph.batch import Evidence, check_batch
from graph.build_input import (
    ChunkRow,
    ExtractFile,
    ExtractNode,
    FilingRow,
    RiskSection,
    StructuralRows,
)
from graph.builder import BODY_PROMPT, build_graph
from ingest.chunker import chunk_filing

PROMPT = "extract/v2@34a82e2b"
TEN_K = "0001045810-26-000021"
TEN_Q = "0001045810-26-000075"

COMPETITION = "Competition could adversely impact our market share and financial results."
SUPPLY = ("Long manufacturing lead times and uncertain supply and capacity availability, "
          "combined with a failure to estimate customer demand accurately, has led and could "
          "lead to mismatches between supply and demand.")
DEPENDENCY = ("Dependency on third-party suppliers and their technology to manufacture, "
              "assemble, test, or package our products")
DEFECTS = "Defects in our products have caused and could cause us to incur significant expenses"
DELAWARE = ("Delaware law and our certificate of incorporation, bylaws and agreement with "
            "Microsoft could delay or prevent a change in control.")
FAILURE = ("Failure to meet the evolving needs of our industry and markets may adversely "
           "impact our financial results.")
FOUNDRIES = "We utilize foundries, such as Taiwan Semiconductor Manufacturing Company"
EXTENDED = "We have previously experienced and may continue to experience extended lead times"
COMMITMENTS = ("Commitments, guarantees, and other commercial arrangements expose us to "
               "financial, counterparty and execution risks")
COMPLEX_LAWS = "We are subject to complex laws, rules, regulations, and political and other actions"


def _chunk(ordinal: int, accession_no: str = TEN_K) -> str:
    return f"{accession_no}:{ordinal:04d}"


def _chunks(accession_no, *ordinals):
    return {_chunk(n, accession_no) for n in ordinals}


SUMMARY_CHUNK = _chunk(19)


def _rows_for(filing, m):
    """Structural rows for one fixture filing, its risk section as Postgres gives it."""
    chunks = chunk_filing(filing)
    section = "part_i_item_1a" if m.form_type == "10-K" else "part_ii_item_1a"
    risk = [c for c in chunks if c.section == section]
    start, end = min(c.char_start for c in risk), max(c.char_end for c in risk)
    rows = StructuralRows(
        filings=(FilingRow(m.accession_no, m.cik, m.company_name, m.form_type,
                           m.fiscal_period, m.filing_date),),
        chunks=tuple(ChunkRow(c.chunk_id, m.accession_no, c.section, c.char_start, c.char_end)
                     for c in chunks),
        risk_sections=(RiskSection(m.accession_no, start, filing.text[start:end]),),
    )
    return rows, {c.chunk_id: filing.text[c.char_start:c.char_end] for c in chunks}


@pytest.fixture(scope="module")
def ten_k(nvda_10k_filing, nvda_10k_meta):
    return _rows_for(nvda_10k_filing, nvda_10k_meta)


@pytest.fixture(scope="module")
def ten_q(nvda_10q_filing, nvda_10q_meta):
    return _rows_for(nvda_10q_filing, nvda_10q_meta)


def _factor(accession_no, title, *evidence, confidence="stated"):
    return ExtractNode("RiskFactor", {"key": _key(accession_no, title), "title": title},
                       tuple(Evidence(c, span, confidence, PROMPT) for c, span in evidence))


def _key(accession_no, title):
    return f"{accession_no}:{title.lower()}"


def _build(rows, *factors, accession_no=TEN_K):
    return build_graph(rows, [ExtractFile(accession_no, 1, "0" * 64, factors, ())], {}, ())


def _evidence(built, title):
    [node] = [n for n in built.batch.nodes
              if n.label == "RiskFactor" and n.properties["title"] == title]
    return {e.chunk_id: e for e in node.evidence}


def _by_prompt(built, title, prompt):
    return {c for c, e in _evidence(built, title).items() if e.extract_prompt == prompt}


# ── where a body starts and ends ──────────────────────────────────────────────

def test_a_body_ends_at_the_next_risk_factor_heading(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, SUPPLY, (_chunk(21), SUPPLY)),
                   _factor(TEN_K, DEPENDENCY, (_chunk(25), DEPENDENCY)),
                   _factor(TEN_K, DEFECTS, (_chunk(26), DEFECTS)))
    assert _by_prompt(built, SUPPLY, PROMPT) == {_chunk(21)}
    assert _by_prompt(built, SUPPLY, BODY_PROMPT) == _chunks(TEN_K, 22, 23, 24, 25)
    assert set(_evidence(built, DEPENDENCY)) == _chunks(TEN_K, 25, 26)


def test_a_body_inside_its_heading_chunk_adds_no_other_chunk(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, COMPETITION, (_chunk(21), COMPETITION)),
                   _factor(TEN_K, SUPPLY, (_chunk(21), SUPPLY)))
    assert set(_evidence(built, COMPETITION)) == {_chunk(21)}


def test_the_last_body_ends_at_the_section_end(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, FAILURE, (_chunk(20), FAILURE)))
    assert set(_evidence(built, FAILURE)) == {_chunk(n) for n in range(20, 53)}


def test_the_summary_list_is_not_a_body(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, COMPETITION, (SUMMARY_CHUNK, COMPETITION),
                                 (_chunk(21), COMPETITION)),
                   _factor(TEN_K, SUPPLY, (SUMMARY_CHUNK, SUPPLY), (_chunk(21), SUPPLY)))
    assert set(_evidence(built, COMPETITION)) == {SUMMARY_CHUNK, _chunk(21)}
    assert _by_prompt(built, COMPETITION, BODY_PROMPT) == set()


def test_a_factor_quoted_from_a_body_sentence_neither_gets_a_body_nor_ends_one(ten_k):
    # q0127 (Step 11): the sentence starts chunk 22 mid-paragraph. Taken as a
    # heading, it would end the supply factor's body at chunk 21.
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, SUPPLY, (_chunk(21), SUPPLY)),
                   _factor(TEN_K, EXTENDED, (_chunk(22), EXTENDED)),
                   _factor(TEN_K, DEPENDENCY, (_chunk(25), DEPENDENCY)))
    assert _by_prompt(built, SUPPLY, BODY_PROMPT) == _chunks(TEN_K, 22, 23, 24, 25)
    assert set(_evidence(built, EXTENDED)) == {_chunk(22)}
    assert built.report.bodies.not_headings == (_key(TEN_K, EXTENDED),)
    assert built.report.bodies.unlocated == ()


def test_a_10q_body_is_read_from_part_ii_item_1a(ten_q):
    rows, _ = ten_q
    built = _build(rows, _factor(TEN_Q, COMMITMENTS, (_chunk(53, TEN_Q), COMMITMENTS)),
                   _factor(TEN_Q, COMPLEX_LAWS, (_chunk(54, TEN_Q), COMPLEX_LAWS)),
                   accession_no=TEN_Q)
    # The next heading starts in the overlap of chunks 54 and 55, so both are touched.
    assert set(_evidence(built, COMMITMENTS)) == _chunks(TEN_Q, 53, 54, 55)


# ── what a body entry holds ───────────────────────────────────────────────────

def test_body_entries_are_stated_and_quote_up_to_200_chars_of_their_own_chunk(ten_k):
    rows, texts = ten_k
    built = _build(rows, _factor(TEN_K, SUPPLY, (_chunk(21), SUPPLY)),
                   _factor(TEN_K, DEPENDENCY, (_chunk(25), DEPENDENCY)))
    body = {c: e for c, e in _evidence(built, SUPPLY).items() if e.extract_prompt == BODY_PROMPT}
    assert body
    for chunk_id, entry in body.items():
        assert entry.confidence == "stated"
        assert 0 < len(entry.span) <= 200
        assert entry.span in texts[chunk_id]
    # Chunk 22 lies wholly inside the body, so its quote is the chunk's first text.
    assert texts[_chunk(22)].lstrip().startswith(body[_chunk(22)].span)
    assert len(body[_chunk(22)].span) == 200
    assert check_batch(built.batch) == ()


def test_the_heading_chunk_keeps_its_extracted_entry(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, SUPPLY, (_chunk(21), SUPPLY)),
                   _factor(TEN_K, DEPENDENCY, (_chunk(25), DEPENDENCY)))
    heading = _evidence(built, SUPPLY)[_chunk(21)]
    assert (heading.extract_prompt, heading.span) == (PROMPT, SUPPLY)


def test_a_body_entry_replaces_a_less_confident_extracted_one(ten_k):
    rows, _ = ten_k
    weak = ExtractNode("RiskFactor", {"key": _key(TEN_K, SUPPLY), "title": SUPPLY},
                       (Evidence(_chunk(21), SUPPLY, "stated", PROMPT),
                        Evidence(_chunk(23), "customer demand", "uncertain", PROMPT)))
    built = _build(rows, weak, _factor(TEN_K, DEPENDENCY, (_chunk(25), DEPENDENCY)))
    assert _evidence(built, SUPPLY)[_chunk(23)].extract_prompt == BODY_PROMPT
    # The weaker span is not taken as the heading: the body is unchanged.
    assert set(_evidence(built, SUPPLY)) == _chunks(TEN_K, 21, 22, 23, 24, 25)


def test_body_entries_are_not_an_extract_prompt_mix(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, FAILURE, (_chunk(20), FAILURE)))
    assert BODY_PROMPT == "body/v1"
    assert {e.extract_prompt for e in _evidence(built, FAILURE).values()} == {PROMPT, BODY_PROMPT}


# ── what the build report lists ───────────────────────────────────────────────

def test_a_heading_outside_the_risk_section_gets_no_body_and_is_counted(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, "Foundries", (_chunk(12), FOUNDRIES)))
    assert set(_evidence(built, "Foundries")) == {_chunk(12)}
    assert built.report.bodies.unlocated == (_key(TEN_K, "Foundries"),)
    assert built.report.bodies.body_entries == 0


def test_a_filing_without_a_risk_section_locates_nothing(ten_k):
    rows, _ = ten_k
    built = _build(StructuralRows(rows.filings, rows.chunks, ()),
                   _factor(TEN_K, FAILURE, (_chunk(20), FAILURE)))
    assert set(_evidence(built, FAILURE)) == {_chunk(20)}
    assert built.report.bodies.unlocated == (_key(TEN_K, FAILURE),)


def test_a_body_over_five_chunks_is_listed_and_not_capped(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, FAILURE, (_chunk(20), FAILURE)))
    assert built.report.bodies.long_bodies == ((_key(TEN_K, FAILURE), 33),)
    assert built.report.bodies.body_entries == 32  # chunk 20 keeps its extracted entry


def test_a_body_of_exactly_five_chunks_is_not_listed(ten_k):
    rows, _ = ten_k
    built = _build(rows, _factor(TEN_K, SUPPLY, (_chunk(21), SUPPLY)),
                   _factor(TEN_K, DEPENDENCY, (_chunk(25), DEPENDENCY)),
                   _factor(TEN_K, DEFECTS, (_chunk(26), DEFECTS)))
    assert _key(TEN_K, SUPPLY) not in dict(built.report.bodies.long_bodies)
    assert built.report.bodies.unlocated == ()
