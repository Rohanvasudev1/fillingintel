# 02: Embeddings in Postgres

**What to build:** the user runs `python -m ingest.embed` once and every chunk, the preamble included, gets a `voyage-4-large` vector in a new `chunk_embeddings` table (ADR-0002). Reruns skip chunks whose stored text hash still matches. An oversized chunk stops the command before any API call.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Adds no dependency. The Voyage client uses httpx
- [ ] `chunk_embeddings` keyed on (chunk_id, model), referencing chunks, holding dimensions, embedded-text SHA-256, the API's token count, the vector and a timestamp. No ANN index
- [ ] `apply_schema()` checks the new table's live columns. The chunks table is unchanged
- [ ] Voyage calls use input type `document`, truncation off, batches within the per-request limit, and the key from VOYAGE_API_KEY, which is added empty to .env.example
- [ ] A chunk over the model's input limit fails the run before any call
- [ ] Tests on the throwaway schema with a fake embedder replaying a recorded Voyage response: one row per chunk, hash and token count stored, rerun skips current rows, oversized chunk fails
- [ ] pytest stays offline. The API key never appears in logs or recorded responses
- [ ] Reviewed with `mattpocock-skills:code-review`, every finding fixed or explained
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass
