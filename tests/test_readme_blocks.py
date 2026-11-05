"""The two generated README blocks (Step 14 ticket 03): built from the same committed
files as ``benchmarks/results.md``, spliced between marker comments, and checked by
``python -m eval.results_table --check``."""
import os
import re
import subprocess
import sys

import pytest

from eval.readme_blocks import (
    BLOCKS,
    ReadmeMarkerError,
    begin_marker,
    end_marker,
    readme_blocks,
    splice,
)
from eval.results_table import EXIT_COULD_NOT_RUN, EXIT_DIFFERS, EXIT_OK, build, main
from ingest.provenance import REPO_ROOT

PROSE = "# Title\n\nSome prose a person wrote.\n\n"


def _readme(*names: str, body: str = "old\n") -> str:
    text = PROSE
    for name in names:
        text += f"{begin_marker(name)}\n{body}{end_marker(name)}\n\nMore prose.\n"
    return text


@pytest.fixture(scope="module")
def blocks() -> dict[str, str]:
    return readme_blocks()


def _row(block: str, first: str) -> str:
    (row,) = [line for line in block.splitlines() if line.startswith(f"| {first} |")]
    return row


def test_splice_rewrites_only_between_the_markers():
    text = _readme(*BLOCKS)
    new = splice(text, {name: f"new {name}\n" for name in BLOCKS})
    for name in BLOCKS:
        assert f"{begin_marker(name)}\nnew {name}\n{end_marker(name)}" in new
    assert new.startswith(PROSE)
    assert new.count("More prose.") == len(BLOCKS)
    assert splice(new, {name: f"new {name}\n" for name in BLOCKS}) == new


def test_splice_refuses_a_missing_or_repeated_marker():
    with pytest.raises(ReadmeMarkerError):
        splice(_readme(BLOCKS[0]), {name: "x\n" for name in BLOCKS})
    doubled = _readme(*BLOCKS) + begin_marker(BLOCKS[0]) + "\n"
    with pytest.raises(ReadmeMarkerError):
        splice(doubled, {name: "x\n" for name in BLOCKS})


def test_the_blocks_are_identical_under_two_hash_seeds(blocks):
    code = ("import sys; from eval.readme_blocks import readme_blocks; "
            "sys.stdout.write(repr(readme_blocks()))")

    def run(seed: str) -> str:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        return subprocess.run([sys.executable, "-c", code], env=env, cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True).stdout

    assert run("1") == run("2") == repr(blocks)


def test_the_headline_table_has_the_four_arms_and_both_routers(blocks):
    table = blocks["test-headline"]
    for row in ("vector", "graph_local", "graph_traversal", "graph_global",
                "router (pre-registered)", "router (dev-best)"):
        assert _row(table, row)
    head = next(line for line in table.splitlines() if line.startswith("| row |"))
    assert "lookup (n 13)" in head and "multi_hop (n 7, directional)" in head
    assert "faithfulness (uncalibrated)" in head


def test_pooled_recall_and_paired_differences_match_the_results_file(blocks):
    table = blocks["test-headline"]
    results = build()
    assert "0.923 [" in _row(table, "vector")  # BUILD-LOG's pooled test recall@10
    paired = results.split("### `test` paired differences")[1].split("## Hypotheses")[0]
    for arm in ("graph_local", "graph_traversal", "router (pre-registered)",
                "router (dev-best)"):
        (line,) = [r for r in paired.splitlines()
                   if r.startswith(f"| {arm} | pooled | recall@10 |")]
        difference = line.split(" | ")[4]
        assert difference in _row(table, arm)


def test_graph_global_pools_its_class_sources_and_says_what_was_retrieval_only(blocks):
    table = blocks["test-headline"]
    row = _row(table, "graph_global")
    assert "| 0.439 [" in row  # global from the full run, the rest retrieval only
    assert row.count("retrieval only") == 3  # faithfulness, cost, latency
    assert "33 of 38 answerable questions" in table


def test_the_headline_names_split_labels_commit_and_the_no_gold_rows(blocks):
    table = blocks["test-headline"]
    assert "47 agent-drafted questions" in table
    assert "`f761581`" in table
    assert "uncalibrated" in table and "directional" in table
    assert "Decline (4) and unanswerable (5)" in table


def test_the_unpublished_numbers_carry_value_label_commit_and_a_link(blocks):
    block = blocks["unpublished-numbers"]
    assert "0.783 [0.600, 0.947]" in block  # ER recall, round 2
    assert "set `scoring2`" in block and "adjudicator accuracy" in block
    assert "38 of 42" in block  # router test accuracy
    assert "0 breaks in 163" in block and "0 breaks in 82" in block
    assert "23 of 24" in block  # corruption: changed number, faithfulness
    for line in block.splitlines()[2:]:
        if line.startswith("| "):
            assert "](benchmarks/results.md#" in line and "`" in line


def test_every_link_points_at_a_heading_in_the_results_file(blocks):
    slugs = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-")
             for h in re.findall(r"^#+ (.+)$", build(), re.M)}
    links = re.findall(r"benchmarks/results\.md#([\w\-]+)", blocks["unpublished-numbers"])
    assert links and set(links) <= slugs


def _write_readme(tmp_path, body: str = "old\n"):
    path = tmp_path / "README.md"
    path.write_text(_readme(*BLOCKS, body=body), encoding="utf-8")
    return path


def test_writing_fills_the_readme_and_check_then_passes(tmp_path):
    output, readme = tmp_path / "results.md", _write_readme(tmp_path)
    args = ["--output", str(output), "--readme", str(readme)]
    assert main(args) == EXIT_OK
    assert readme.read_text(encoding="utf-8").startswith(PROSE)
    assert main(["--check", *args]) == EXIT_OK
    edited = readme.read_text(encoding="utf-8").replace("Some prose", "Edited prose")
    readme.write_text(edited, encoding="utf-8")
    assert main(["--check", *args]) == EXIT_OK  # prose outside the markers is free


def test_check_exits_4_when_a_readme_block_differs(tmp_path, capsys):
    output, readme = tmp_path / "results.md", _write_readme(tmp_path)
    args = ["--output", str(output), "--readme", str(readme)]
    assert main(args) == EXIT_OK
    readme.write_text(readme.read_text(encoding="utf-8").replace("| vector |", "| vectr |"),
                      encoding="utf-8")
    assert main(["--check", *args]) == EXIT_DIFFERS
    assert "README" in capsys.readouterr().err


def test_a_readme_without_markers_cannot_run(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(PROSE, encoding="utf-8")
    assert main(["--check", "--output", str(tmp_path / "r.md"), "--readme", str(readme)]) \
        == EXIT_COULD_NOT_RUN
