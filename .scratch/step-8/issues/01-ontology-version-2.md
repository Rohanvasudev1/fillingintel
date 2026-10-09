# 01: Ontology version 2

**What to build:** evidence that says how sure the extractor was and which prompt produced it. Extracted edges gain required `confidences` and `extract_prompts` lists, parallel to `chunk_ids` and `evidence_spans`; `EVIDENCED_BY` gains required `confidence` and `extract_prompt`. Confidence is one of `stated`, `implied` or `uncertain`. `ONTOLOGY_VERSION` becomes 2, the schema text and its hash change, and the batch check, the write path and `validate_graph()` enforce the new rules. Nodes carry neither property (spec: Ontology version 2; ADR-0004 extended, not reversed).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The ontology defines the four new required properties and the confidence enum; the generated schema text lists them, and the pinned schema hash test is updated to version 2.
- [ ] The batch check rejects, one test per case: a missing `confidences` or `extract_prompts` list; a list whose length differs from `chunk_ids`; a confidence outside the enum (on an edge and on node evidence); an `EVIDENCED_BY` without `extract_prompt`; a blank prompt version.
- [ ] Node evidence records and edge evidence carry the new fields; the write path stores them, and appending a chunk to an existing edge appends its confidence and prompt version at the same index.
- [ ] `validate_graph()` reports each of the cases above found in a live graph, each broken graph made with raw Cypher and failing exactly its own check.
- [ ] The shared Step 7 test graph is updated to version 2 and stays valid; every span is still checked against its chunk's text.
- [ ] Writing to or applying constraints on a graph whose `:GraphMeta` holds version 1 is refused, as now. The local Neo4j's version-1 `:GraphMeta` is left alone; clearing it is the user's step at the start of Step 10 (recorded in BUILD-LOG).
- [ ] CLAUDE.md's Step 7 property list is not edited; the Step 8 Decisions entry already records version 2.
- [ ] Rule tests written first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review` (Cypher axis). `uv run ruff check .` and `uv run --env-file .env pytest` pass, and CI is green.
