# 02: Neo4j test instance and constraints

**What to build:** the ontology's constraints applied to a live Neo4j and proven to match the code, with a test database that can't be confused with the real graph. `apply_constraints(driver)` creates every uniqueness constraint (named from the ontology) with `IF NOT EXISTS`, reads `SHOW CONSTRAINTS` back, and raises `ConstraintMismatch` on anything missing, different or extra on an ontology label. It then creates the single `:GraphMeta` node holding the ontology version and schema hash, or refuses if one exists with another version or hash. Tests run against a separate `neo4j-test` instance locally and a Neo4j service in CI. See docs/research/neo4j-driver-and-constraints.md, sections 2, 5 and 7.

**Blocked by:** 01 (Ontology module and schema text)

**Status:** done (2026-10-07, PR #13)

- [x] `neo4j==6.3.1` added as a direct dependency (approved in the grilling); `uv.lock` updated. The driver is created with `telemetry_disabled=True` and every query names the database.
- [x] docker-compose gains a `neo4j-test` service from the same pinned image, bound to 127.0.0.1:7688, with its own volume and credentials from environment variables. `.env.example` gains `NEO4J_TEST_URI`, `NEO4J_TEST_USER`, `NEO4J_TEST_PASSWORD`.
- [x] CI's `lint-and-test` job gains a Neo4j service from the same image with a health check and the `NEO4J_TEST_*` variables. The `quality-gate` job is unchanged. (This changes CI; approved in the grilling.)
- [x] The test fixture creates a marker node only in an empty database, fails (not skips) when the database has data but no marker, and wipes everything but the marker between tests.
- [x] Graph tests skip when `NEO4J_TEST_URI` is unset; a guard test fails in CI if it is unset.
- [x] Uniqueness constraints exist for `Company.cik`, `Filing.accession_no`, `Chunk.chunk_id`, Period (`cik`, `fiscal_period`) and `key` on each extracted label, shown by `SHOW CONSTRAINTS` in a test.
- [x] Running `apply_constraints` twice changes nothing.
- [x] A constraint created beforehand under an ontology constraint name but on a different property raises `ConstraintMismatch`; so does an extra constraint on an ontology label.
- [x] A `:GraphMeta` with another version or hash makes `apply_constraints` refuse without changing it.
- [x] Inserting two nodes with the same key raises the database's uniqueness error.
- [x] Code review with `mattpocock-skills:code-review` (Cypher: injection safety, indexes, constraints). `uv run ruff check .` and `uv run --env-file .env pytest` pass, and CI is green on the PR.
