# 02: Corpus snapshot and snapshot query embedder

**What to build:** a committed snapshot that lets an empty Postgres hold the same searchable corpus as the researcher's local database, with no network. The existing fixture builder script writes one gzipped file from the local database and the local query embedding cache: the `filings` rows (with parsed text), the `chunks` rows, the `chunk_embeddings` rows for `voyage-4-large`, and the query vectors for the 82 answerable `dev` questions of the agent-drafted set, keyed by model and question text hash. A loader applies the real schema and inserts the rows, so the schema's CHECKs run, and returns the snapshot's SHA-256. A snapshot query embedder implements the existing `QueryEmbedder` interface from the snapshot's query vectors. See the spec's Snapshot section and ADR-0003.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Rebuilding the snapshot with unchanged data gives a byte-identical file (stable row order, no timestamps).
- [ ] The snapshot holds no `test` split query vectors, no decline or unanswerable query vectors, and no secrets.
- [ ] Round trip test: building from a database and loading into an empty throwaway schema gives identical rows in `filings`, `chunks` and `chunk_embeddings`.
- [ ] Loading a snapshot with a changed byte, or whose rows fail a schema CHECK, fails loudly.
- [ ] The snapshot query embedder returns the stored vector for a gated question and raises for any other question; it never calls Voyage.
- [ ] A test compares the committed snapshot with the local database and query cache when both are present, and skips otherwise (prior art: the `corpus_filings.json` check against `data/parsed`).
- [ ] The snapshot is built from the local database, committed, and its size is reported in the BUILD-LOG entry (estimate 10–12 MB).
- [ ] No schema change.
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass.
