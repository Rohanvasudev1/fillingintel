"""Reading extract files for the graph builder (Step 10).

Files are written by extraction's own writer (extract.report), so the reader
is tested against the format extraction produces, from the shared test graph.
"""
import json

import pytest

from extract.pipeline import ExtractionRun
from extract.report import candidate_lines, write_candidates
from graph.build_input import (
    InputError,
    latest_extract_files,
    read_extract_file,
)
from graph.ontology import LABELS_BY_NAME, Kind
from tests.graph_test_data import NVDA_10K, TEST_PROMPT


def _extracted(batch):
    nodes = tuple(n for n in batch.nodes if LABELS_BY_NAME[n.label].kind is Kind.EXTRACTED)
    edges = tuple(e for e in batch.edges if "chunk_ids" in e.properties)
    return nodes, edges


def _write(path, batch):
    nodes, edges = _extracted(batch)
    run = ExtractionRun(NVDA_10K, TEST_PROMPT, nodes, edges, (), (), (), (), (), ())
    write_candidates(path, candidate_lines(run))


def test_a_file_written_by_extraction_reads_back_as_its_records(tmp_path, graph_test_batch):
    path = tmp_path / f"{NVDA_10K}-run3.jsonl"
    _write(path, graph_test_batch)
    found = read_extract_file(path)
    nodes, edges = _extracted(graph_test_batch)
    assert (found.accession_no, found.run, found.name) == (NVDA_10K, 3, path.name)
    assert [(n.label, dict(n.properties), n.evidence) for n in found.nodes] == [
        (n.label, dict(n.properties), n.evidence) for n in nodes]
    huang_roles = sorted(e.role for e in found.edges if e.type == "HOLDS_ROLE_AT")
    assert huang_roles == ["Chief Executive Officer", "President"]
    assert [(e.type, e.start, e.end, [x.chunk_id for x in e.evidence]) for e in found.edges] == [
        (e.type, e.start, e.end, list(e.properties["chunk_ids"])) for e in edges]
    assert len(found.sha256) == 64


@pytest.mark.parametrize("line, message", [
    ({"kind": "summary"}, "unknown kind"),
    ({"kind": "node", "label": "Organization", "properties": {"key": "k"}, "evidence": []},
     "evidence"),
    ({"kind": "edge", "type": "SUPPLIES", "start": {"label": "Organization", "key": {"key": "k"}},
      "end": {"label": "Company", "key": {"cik": "1045810"}},
      "properties": {"chunk_ids": ["a"], "evidence_spans": [], "confidences": ["stated"],
                     "extract_prompts": ["p"]}}, "one length"),
])
def test_a_line_extraction_never_writes_is_refused_with_its_line_number(tmp_path, line, message):
    path = tmp_path / f"{NVDA_10K}-run1.jsonl"
    path.write_text(json.dumps({"kind": "flag"}) + "\n" + json.dumps(line) + "\n")
    with pytest.raises(InputError, match=f"line 2: .*{message}"):
        read_extract_file(path)


def test_the_latest_run_of_each_filing_is_chosen(tmp_path):
    for name in (f"{NVDA_10K}-run1.jsonl", f"{NVDA_10K}-run3.jsonl", f"{NVDA_10K}-run10.jsonl",
                 "0000002488-26-000018-run2.jsonl", "notes.txt"):
        (tmp_path / name).write_text("")
    assert [p.name for p in latest_extract_files(tmp_path)] == [
        "0000002488-26-000018-run2.jsonl", f"{NVDA_10K}-run10.jsonl"]


def test_the_load_command_exits_2_on_bad_arguments_and_3_without_settings(monkeypatch, capsys):
    from graph.load import main
    with pytest.raises(SystemExit) as exc:
        main(["--rebuld"])
    assert exc.value.code == 2
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main([]) == 3
    assert "DATABASE_URL" in capsys.readouterr().err
