# 03: Guarded write path

**What to build:** the one way into the graph. A pure batch check takes nodes and edges as typed records plus the set of chunk IDs already in the graph, and returns every violation: unknown label or edge type, disallowed endpoints, missing or wrongly typed required properties, an extracted node with no evidence, an extracted edge with no `chunk_ids` or with `evidence_spans` of a different length, and a chunk ID that is neither in the batch nor in the graph. `write_batch(driver, batch)` runs the check first and, on any violation, raises with the full list and writes nothing. A valid batch is written in one transaction. Nodes merge on their keys; edges merge on (start key, type, end key, role where present), appending new chunk IDs with their spans and skipping chunks already listed. Node evidence accumulates through `EVIDENCED_BY` edges the same way (ADR-0004). This ticket also builds the shared valid test graph.

**Blocked by:** 02 (Neo4j test instance and constraints)

**Status:** done (2026-10-07, PR #14)

- [x] A shared valid test graph: structural nodes for fixture filings, Chunk nodes with real chunk IDs from running the chunker on the gzipped fixture filings, and hand-written extracted nodes and edges (for example NVIDIA's 10-K naming TSMC as its foundry). The helper checks that every span is a substring of its chunk's text.
- [x] Pure tests, one violation each, for every rule above, plus one showing a batch with several violations reports all of them.
- [x] A valid batch is written and reads back with every property, including `ontology_version`.
- [x] A batch with one bad item leaves the database exactly as before.
- [x] Writing the same edge twice with a new chunk gives one edge with both chunks and spans; writing it again with a chunk already listed adds nothing.
- [x] One person with two roles at one company gives two `HOLDS_ROLE_AT` edges.
- [x] Labels and edge types reach Cypher only from the ontology, in backticks; every value is a parameter; no `$(...)` dynamic labels. A test passes a label or property value containing Cypher and shows it is rejected or stored as plain data.
- [x] `MetricValue.value` is FLOAT in the ontology: the batch check accepts an int there and the write path stores it as a float, so a whole-number figure is not rejected (user decision, 2026-10-07).
- [x] `git_state()` counts changes under `graph/` as +dirty, alongside ingest/, db/, eval/, retrieve/, prompts/, tests/ and scripts/ (user decision, 2026-10-07).
- [x] Rule tests written first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review` (Cypher axis). `uv run ruff check .` and `uv run --env-file .env pytest` pass, and CI is green.
