# 04: Cypher guard and executor

**What to build:** A query string goes in. Out comes either its rows, or the first guard rule it broke, with a message a repair prompt can use. Nothing that writes, loads files, ends transactions or runs procedures can reach Neo4j through it. Spec: `.scratch/step-11/spec.md` (Cypher guard and executor). Decisions: ADR-0007. Research: docs/research/text-to-cypher-and-local-search.md (sections A.1–A.5).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Parallel sessions:** other Step 11 tickets may run at the same time in their own worktrees. `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, and iterate with `NEO4J_TEST_URI` unset when your tests do not need Neo4j. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket.

- [ ] Check 1, text rules: a single statement after stripping comments and string literals. Deny `LOAD CSV`, `TERMINATE`, `SHOW`, `USE`, `CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE`, `DROP`, `FOREACH` and any procedure `CALL`; `CALL {` and `CALL (` subqueries are allowed.
- [ ] Check 2, the ontology check from the code ontology: every label, edge type and property must exist; patterns with labels on both ends must be allowed endpoints in the written direction (an undirected pattern passes if either direction is allowed); unlabelled ends are not checked.
- [ ] Check 3, `EXPLAIN` in a READ session: query type `r` and no 01N50, 01N51 or 01N52 notification.
- [ ] Check 4, execution in a READ transaction with a 10 s timeout, through an explicit transaction (no driver retry), reading at most 1,000 rows. The result must have a `chunk_ids` column of string lists.
- [ ] Errors and timeouts come back as categorised guard results (`invalid`, `error`, `timeout`), never as exceptions.
- [ ] Compose sets `db.transaction.timeout=15s` and `db.memory.transaction.max=512m` on `neo4j` and `neo4j-test`, and the CI Neo4j service gets the same. Ask the user before the CI change (CLAUDE.md: ask before changing CI).
- [ ] One read-session helper is shared by the executor and `graph.multihop`.
- [ ] Tests on `neo4j-test` (prior art: the one-break-per-case validator tests), each bad query breaking exactly one rule: `LOAD CSV`, `TERMINATE TRANSACTIONS`, `SHOW`, a write clause, a procedure `CALL`, two statements, an unknown label, type or property, a reversed edge, a missing `chunk_ids` column, a timeout. A valid query returns its rows. A test proves READ mode refuses a write even with the text rules bypassed.
- [ ] `mattpocock-skills:code-review` (Standards axis: injection safety); ruff and pytest green; BUILD-LOG entry.
