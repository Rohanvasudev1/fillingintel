"""Tests for data/parsed/{accession_no}.json: the parsed filings the loader reads (Step 3c)."""
import pytest

from ingest.parsed_files import ParsedRecord, read_parsed, read_parsed_dir, write_parsed


def test_round_trip_keeps_meta_text_and_sections(tmp_path, fixture_records):
    record = fixture_records[0]
    path = write_parsed(record, tmp_path)
    assert path == tmp_path / f"{record.meta.accession_no}.json"
    back = read_parsed(path)
    assert back == record
    assert back.filing.text == record.filing.text


def test_read_dir_returns_records_sorted_by_accession(tmp_path, fixture_records):
    for record in fixture_records:
        write_parsed(record, tmp_path)
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    records = read_parsed_dir(tmp_path)
    assert [r.meta.accession_no for r in records] == sorted(
        r.meta.accession_no for r in fixture_records
    )


def test_record_rejects_meta_and_filing_for_different_filings(fixture_records):
    a, b = fixture_records[0], fixture_records[1]
    with pytest.raises(ValueError, match="accession"):
        ParsedRecord(meta=a.meta, filing=b.filing, parser_commit="test")


def test_file_named_for_another_accession_is_rejected(tmp_path, fixture_records):
    record = fixture_records[0]
    path = write_parsed(record, tmp_path)
    wrong = path.with_name("0000000000-00-000000.json")
    path.rename(wrong)
    with pytest.raises(ValueError, match="file name"):
        read_parsed(wrong)


def test_missing_directory_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_parsed_dir(tmp_path / "absent")


def test_write_leaves_no_temporary_file(tmp_path, fixture_records):
    write_parsed(fixture_records[0], tmp_path)
    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]
