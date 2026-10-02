"""Generalisation check over every filing cached in data/raw/.

data/ is gitignored, so this runs on a developer machine after
``uv run python -m ingest.corpus`` has downloaded the manifest, and is skipped
in CI (no network, no corpus).  The six committed fixtures are tested in
test_parser.py and test_parser_substance.py; this checks the position rules
generalise beyond them.
"""
import re
from datetime import date
from pathlib import Path

import pytest

from ingest.corpus import MIN_SECTION_CHARS
from ingest.models import FilingMeta
from ingest.parser import REQUIRED_SECTIONS_10K, REQUIRED_SECTIONS_10Q, parse_filing

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
FILES = sorted(RAW_DIR.glob("*.html")) if RAW_DIR.is_dir() else []
MIN_REQUIRED_CHARS = MIN_SECTION_CHARS
START_KEYWORD = {
    "part_i_item_1a": "risk factors",
    "part_ii_item_1a": "risk factors",
    "part_ii_item_7": "management",
    "part_i_item_2": "management",
    "part_ii_item_8": "statements",
    "part_i_item_1": "statements",
}
_DOC_TYPE = re.compile(r"dei:DocumentType[^>]*>\s*(10-[KQ])\s*<", re.IGNORECASE)

pytestmark = pytest.mark.skipif(not FILES, reason="no filings cached in data/raw/")


def _doc_type(path: Path) -> str:
    m = _DOC_TYPE.search(path.read_text(encoding="utf-8", errors="replace"))
    assert m, f"no dei:DocumentType in {path.name}"
    return m.group(1).upper()


def _parse(path: Path):
    html = path.read_text(encoding="utf-8", errors="replace")
    meta = FilingMeta(
        cik=str(int(path.stem[:10])),  # filer prefix; these companies self-file
        accession_no=path.stem,
        form_type=_doc_type(path),
        company_name="X",
        fiscal_period="FY0000",
        report_date=date(2000, 1, 1),
        filing_date=date(2000, 1, 1),
        primary_document="x.htm",
    )
    return parse_filing(html, meta)


@pytest.fixture(scope="module", params=FILES, ids=lambda p: p.stem)
def parsed(request):
    """``(ParsedFiling, markdown)`` for one cached filing, parsed once per module."""
    from edgar.documents import parse_html

    html = request.param.read_text(encoding="utf-8", errors="replace")
    return _parse(request.param), parse_html(html).to_markdown()


@pytest.fixture
def filing(parsed):
    return parsed[0]


@pytest.fixture
def markdown(parsed):
    return parsed[1]


def _required(filing) -> frozenset[str]:
    return REQUIRED_SECTIONS_10K if filing.form_type == "10-K" else REQUIRED_SECTIONS_10Q


def _text(filing, key: str) -> str:
    """All spans of *key* in document order, joined (an item may continue after another)."""
    spans = sorted((s for s in filing.sections if s.label == key), key=lambda s: s.char_start)
    assert spans, f"{filing.accession_no}: section {key} missing"
    return "\n\n".join(filing.text[s.char_start:s.char_end] for s in spans)


def test_no_missing_required_sections(filing):
    assert filing.missing_sections == []


def test_required_sections_are_substantial_or_documented_pointers(filing):
    for key in _required(filing):
        text = _text(filing, key)
        if len(text) >= MIN_REQUIRED_CHARS:
            continue
        # Documented exception: NVIDIA's Item 8 points to the statements in Item 15.
        assert key == "part_ii_item_8" and "set forth" in text, (
            f"{filing.accession_no}/{key} is only {len(text)} chars: {text[:120]!r}"
        )
        item15 = _text(filing, "part_iv_item_15")
        assert len(item15) > 50_000


def test_required_sections_start_at_their_title(filing):
    for key in _required(filing):
        head = _text(filing, key).splitlines()[0].lower()
        assert START_KEYWORD[key] in head, f"{filing.accession_no}/{key}: {head!r}"


def test_required_sections_do_not_start_on_a_toc_row(filing):
    for key in _required(filing):
        first_line = _text(filing, key).splitlines()[0]
        assert not re.search(r"\|\s*(?:\d{1,3}|Page)\s*\|\s*$", first_line), (
            f"{filing.accession_no}/{key}: {first_line!r}"
        )


def test_sections_reproduce_the_whole_filing(filing, markdown):
    """Every character of the filing's markdown is covered by some section's offsets."""
    assert "".join(filing.text.split()) == "".join(markdown.split())


INTEL_10KS = [p for p in FILES if p.stem.startswith("0000050863") and "10-K" in _doc_type(p)]


@pytest.mark.parametrize("path", INTEL_10KS, ids=lambda p: p.stem)
def test_critical_accounting_estimates_belong_to_item_7(path):
    """Intel prints them after Item 7A; its index lists them under Item 7."""
    filing = _parse(path)
    assert "Critical Accounting Estimates" in _text(filing, "part_ii_item_7")
    assert "Critical Accounting Estimates" not in _text(filing, "part_ii_item_7a")


def test_sections_are_in_document_order_and_disjoint(filing):
    ordered = sorted(filing.sections, key=lambda s: s.char_start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.char_end <= b.char_start
