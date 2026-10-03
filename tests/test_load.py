"""Tests for ``python -m ingest.load``: load data/parsed into Postgres and verify resolve()."""
from ingest.load import EXIT_USAGE, main, run, verify_chunks
from ingest.models import ParsedFiling, ParsedSection
from ingest.parsed_files import ParsedRecord, write_parsed
from ingest.store import load_filing


def _write_all(tmp_path, records):
    parsed_dir = tmp_path / "parsed"
    for record in records:
        write_parsed(record, parsed_dir)
    return parsed_dir


def test_load_then_verify_writes_a_passing_report(tmp_path, db_conn, fixture_records):
    parsed_dir = _write_all(tmp_path, fixture_records)
    report_path = tmp_path / "load_report.txt"
    assert run(db_conn, parsed_dir, verify=True, report_path=report_path) == 0
    report = report_path.read_text(encoding="utf-8")
    assert "- filings loaded: 6" in report
    assert "- random sample: 20 of 20 resolved exactly (seed 20261003)" in report
    assert "- all chunks via resolve(): " in report and "mismatches: 0" in report
    assert "- result: PASS" in report
    assert "- commit:" in report


def test_verify_catches_text_that_no_longer_matches(tmp_path, db_conn, fixture_records):
    parsed_dir = _write_all(tmp_path, fixture_records)
    run(db_conn, parsed_dir, verify=False, report_path=None)
    accession_no = fixture_records[0].meta.accession_no
    with db_conn.transaction():
        db_conn.execute(
            "UPDATE filings SET parsed_text = 'X' || parsed_text,"
            " text_sha256 = encode(sha256(convert_to('X' || parsed_text, 'UTF8')), 'hex')"
            " WHERE accession_no = %s",
            (accession_no,),
        )
    try:
        assert run(db_conn, parsed_dir, verify=True, report_path=None, load=False) == 1
    finally:
        run(db_conn, parsed_dir, verify=False, report_path=None)  # restore


def test_main_without_database_url_fails_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["--parsed-dir", str(tmp_path)]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_main_with_missing_parsed_dir_fails_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@localhost:1/none")
    assert main(["--parsed-dir", str(tmp_path / "absent")]) == 2
    assert "absent" in capsys.readouterr().err


def test_empty_parsed_dir_is_an_error_not_a_pass(tmp_path, db_conn):
    empty = tmp_path / "parsed"
    empty.mkdir()
    assert run(db_conn, empty, verify=True, report_path=None) == EXIT_USAGE


def test_filing_with_no_chunks_fails_verification(db_conn, fixture_records):
    meta = fixture_records[0].meta.model_copy(update={"accession_no": "0000000000-26-999999"})
    blank = ParsedFiling(
        accession_no=meta.accession_no,
        cik=meta.cik,
        form_type=meta.form_type,
        fiscal_period=meta.fiscal_period,
        text="   ",
        sections=[ParsedSection(label="preamble", char_start=0, char_end=3)],
    )
    record = ParsedRecord(meta=meta, filing=blank, parser_commit="test")
    load_filing(db_conn, record, ())
    result = verify_chunks(db_conn, [record])
    assert not result.passed
    assert result.empty_filings == (meta.accession_no,)


def test_malformed_database_url_is_not_echoed(monkeypatch, tmp_path, capsys):
    (tmp_path / "parsed").mkdir()
    monkeypatch.setenv("DATABASE_URL", "secretjunk with spaces")
    assert main(["--parsed-dir", str(tmp_path / "parsed")]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "secretjunk" not in err and "could not connect" in err
