# 04: Graph builder and loader (no persistence links yet)

**What to build:** `python -m graph.load` rebuilds the whole graph from Postgres, the extract files and the alias map in one transaction, runs `validate_graph()` and writes a committed load report. Persistence links come in ticket 07. Spec: `.scratch/step-10/spec.md` (Graph builder, Loader command). ADRs: 0004, 0006.

**Blocked by:** 01, 03

**Status:** ready-for-agent

Builder (pure, test-first with real fixture chunk IDs and spans):
- [ ] Company, Filing, Period and a Chunk node for every chunk come from structural rows; Chunk nodes hold no text.
- [ ] Per filing, the latest extract run under `extract/v2` is used; a mix of prompt versions is refused.
- [ ] Every extracted key maps through the alias map; a key absent from it is its own canonical key; a filer maps to `Company:{cik}`.
- [ ] A merged node takes its canonical member's properties; the other members' names go in `aliases`, sorted, without repeats.
- [ ] A merged edge holds every member's evidence ordered by filing date, then chunk, one entry per chunk (more confident wins, ties to the later). A differing single-valued edge property takes the most confident value, ties to the later filing, and is counted as a conflict with both values.
- [ ] One `EVIDENCED_BY` per (node, chunk), the more confident entry winning.
- [ ] The two Intel metric values without a MEASURES edge are left out and listed; chunk 0000050863-24-000076:0017 is listed as a gap.
- [ ] The builder's output passes `check_batch()`.

Loader:
- [ ] Refuses a non-empty graph without `--rebuild`; with it, deletes everything except `:GraphMeta` and writes, in one `execute_write`. A failed write leaves the previous graph.
- [ ] Runs `validate_graph()` and writes `benchmarks/graph/load-run{N}.json`: commit, alias map hash, extract file hashes, ontology version, counts per label and edge type, conflicts, skipped items, gap, write time, validation result. Exit codes 0 / 2 / 3 / 4 as in `graph.validate`.
- [ ] End-to-end test: the fixture filings load into `neo4j-test` and validate with zero violations (skips without NEO4J_TEST_URI, guarded in CI).
- [ ] The real corpus loads into the local Neo4j after `apply_constraints()`; zero violations; the load report is committed.
- [ ] `mattpocock-skills:code-review` (Cypher: injection safety, indexes, constraints); ruff and pytest green.
