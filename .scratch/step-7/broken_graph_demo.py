"""Step 7 ticket 05: the broken-graph demonstration on neo4j-test (one-off).

Run from the repo root:
  PYTHONPATH=. uv run --env-file .env python .scratch/step-7/broken_graph_demo.py
It reads chunk text from DATABASE_URL and writes only to NEO4J_TEST_*.

Loads the valid test graph, runs `python -m graph.validate` against it, then
breaks one thing at a time (an unevidenced node, then an unevidenced edge)
and runs the command again. Wipes the instance back to its marker at the end.
"""
import os
import subprocess
import sys

import psycopg

from graph.connection import DATABASE, driver_from_env
from graph.constraints import apply_constraints
from graph.validate import EXIT_OK, EXIT_VIOLATIONS
from graph.write import write_batch
from tests.graph_test_data import QUOTES, valid_test_batch
from tests.neo4j_fixtures import (
    MARKER_LABEL,
    TEST_ENV_PREFIX,
    claim_test_database,
    wipe_except_marker,
)

BREAKS = {
    "unevidenced node (TSMC's EVIDENCED_BY edge deleted)":
        "MATCH (:Organization {key: 'org:tsmc'})-[e:EVIDENCED_BY]->() DELETE e",
    "unevidenced edge (SUPPLIES loses chunk_ids and evidence_spans)":
        "MATCH ()-[r:SUPPLIES]->() REMOVE r.chunk_ids, r.evidence_spans",
}


def chunk_rows(url):
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT c.chunk_id, c.section, "
            "substr(f.parsed_text, c.char_start + 1, c.char_end - c.char_start) "
            "FROM chunks c JOIN filings f USING (accession_no) WHERE c.chunk_id = ANY(%s)",
            (list(QUOTES),),
        ).fetchall()
    return {r[0]: r[2] for r in rows}, {r[0]: r[1] for r in rows}


def run_validate():
    env = {**os.environ, **{f"NEO4J_{s}": os.environ[f"{TEST_ENV_PREFIX}_{s}"]
                            for s in ("URI", "USER", "PASSWORD")}}
    print(f"$ NEO4J_URI={env['NEO4J_URI']} python -m graph.validate", flush=True)
    code = subprocess.run([sys.executable, "-m", "graph.validate"], env=env).returncode
    print(f"exit={code}\n", flush=True)
    return code


def fresh_graph(driver, batch):
    wipe_except_marker(driver)
    driver.execute_query(f"MATCH (m:`{MARKER_LABEL}`) DELETE m", database_=DATABASE)
    apply_constraints(driver)
    write_batch(driver, batch)


def main():
    texts, sections = chunk_rows(os.environ["DATABASE_URL"])
    batch = valid_test_batch(texts, sections)
    driver = driver_from_env(TEST_ENV_PREFIX)
    try:
        claim_test_database(driver)
        fresh_graph(driver, batch)
        print("== valid test graph ==")
        codes = [run_validate()]
        for name, query in BREAKS.items():
            fresh_graph(driver, batch)
            driver.execute_query(query, database_=DATABASE)
            print(f"== broken: {name} ==\n$ {query}")
            codes.append(run_validate())
    finally:
        wipe_except_marker(driver)
        driver.execute_query(f"MERGE (:`{MARKER_LABEL}`)", database_=DATABASE)
        records = driver.execute_query("MATCH (n) RETURN count(n) AS n", database_=DATABASE).records
        nodes = records[0]["n"]
        print(f"neo4j-test wiped: {nodes} node(s) left (the test marker)")
        driver.close()
    return 0 if codes == [EXIT_OK, EXIT_VIOLATIONS, EXIT_VIOLATIONS] else 1


if __name__ == "__main__":
    sys.exit(main())
