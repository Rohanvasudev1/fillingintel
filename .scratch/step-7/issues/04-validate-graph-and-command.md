# 04: validate_graph() and `python -m graph.validate`

**What to build:** a whole-graph check that fails loudly on any node or edge that breaks the ontology or evidence rules, however it got into the database. `validate_graph(driver, postgres=None)` reads the graph and checks: every node has exactly one ontology label (or is `:GraphMeta`); every edge type is known; endpoints are allowed; required properties are present with the right types; extracted nodes have at least one `EVIDENCED_BY` edge to a Chunk; extracted edges have non-empty `chunk_ids` that are all existing Chunk nodes, with `evidence_spans` of the same length; each Filing has one `FILED` and one `COVERS_PERIOD`; each Chunk has one `PART_OF`; `:GraphMeta` matches the code's version and hash; and, with a Postgres connection, each Chunk node's ID exists in the `chunks` table under the same filing. It collects every violation and raises `GraphValidationError` with counts per check and up to 20 examples each. `python -m graph.validate` runs it from the environment and exits 0 valid, 2 bad input, 3 could not run, 4 violations found.

**Blocked by:** 03 (Guarded write path)

**Status:** ready-for-agent

- [ ] The valid test graph and an empty graph with a correct `:GraphMeta` both pass.
- [ ] One test per broken case, each breaking one thing in the valid graph with raw Cypher that goes around the write path: unknown label, two ontology labels on one node, unknown edge type, disallowed endpoint, missing property, wrong property type, extracted node without evidence, empty `chunk_ids`, dangling chunk ID, span and chunk lists of different lengths, Filing without `FILED`, Chunk without `PART_OF`, wrong `:GraphMeta`.
- [ ] A Chunk node whose ID is not in Postgres, or is under another filing, fails the Postgres check (existing throwaway-schema Postgres fixture). Postgres queries are parameterised.
- [ ] A graph with several kinds of violation reports all of them with correct counts, and examples are capped at 20 per check.
- [ ] The command prints which checks ran and whether the Postgres check ran or was skipped.
- [ ] Exit codes tested through `main(argv)`: 0, 2, 3 (missing settings, Neo4j unreachable) and 4.
- [ ] Tests written first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review` (Cypher and SQL axes). `uv run ruff check .` and `uv run --env-file .env pytest` pass, and CI is green.
