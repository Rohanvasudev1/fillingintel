# 01: Write path: named sets, evidence as given, duplicate items rejected

**What to build:** `write_batch()` writes exactly what the batch holds. Properties are set one by one by name, so a null value never deletes a property and the write plans no Eager step. Evidence lists and `EVIDENCED_BY` entries are written as given, replacing the Step 7 append and first-wins rules. `check_batch()` rejects a batch holding the same item twice. Spec: `.scratch/step-10/spec.md` (Write path). Research: docs/research/neo4j-bulk-load.md.

**Blocked by:** None (can start immediately)

**Status:** done (2026-10-13)

- [x] A test first pins what today's code does with the same edge twice in one batch; the result is reported in the PR.
- [x] `check_batch()` reports a violation for the same node key twice, or the same edge identity (start, type, end, role where the type has one) twice, naming the item. A batch with such a violation writes nothing.
- [x] Node and edge properties are set by name from the ontology; no `SET +=` remains. A test shows writing a node or edge again with an optional property absent leaves it as the batch says, and no null deletes a property set in the same batch.
- [x] An extracted edge's four evidence lists and each `EVIDENCED_BY` edge's span, confidence and prompt are the batch's values after the write, whether or not the item existed before.
- [x] `EXPLAIN` of each node and edge write shows no Eager operator (test against `neo4j-test`).
- [x] The module docstring describes the new rules; "Step 10 replaces this" is gone.
- [x] All existing graph tests pass, updated only where they asserted the old append or first-wins rule, each such change named in the PR.
- [x] Test-first for each rule; `mattpocock-skills:code-review` (Cypher: injection safety, indexes, constraints); ruff and pytest green.
