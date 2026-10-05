# 02: Embeddings in Postgres

**What to build:** the user runs `python -m ingest.embed` once and every chunk, the preamble included, gets a `voyage-4-large` vector in a new `chunk_embeddings` table (ADR-0002). Reruns skip chunks whose stored text hash still matches. An oversized chunk stops the command before any API call.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [x] Adds no dependency. The Voyage client uses httpx
- [x] `chunk_embeddings` keyed on (chunk_id, model), referencing chunks, holding dimensions, embedded-text SHA-256, the API's token count, the vector and a timestamp. No ANN index
- [x] `apply_schema()` checks the new table's live columns. The chunks table is unchanged
- [x] Voyage calls use input type `document`, truncation off, batches within the per-request limit, and the key from VOYAGE_API_KEY, which is added empty to .env.example
- [x] A chunk over the model's input limit fails the run before any call
- [x] Tests on the throwaway schema with a fake embedder replaying a recorded Voyage response: one row per chunk, hash and token count stored, rerun skips current rows, oversized chunk fails
- [x] pytest stays offline. The API key never appears in logs or recorded responses
- [x] Reviewed with `mattpocock-skills:code-review`, every finding fixed or explained
- [x] `uv run ruff check .` and `uv run --env-file .env pytest` pass

## Comments

**2026-10-05 (agent, implementation).** Done except for one box: the tests run on a placeholder Voyage response, not a recorded one.

- **Recorded response.** No `VOYAGE_API_KEY` was set, so `tests/fixtures/voyage/document_response.json` uses Voyage's documented response shape with seeded random floats. Its README labels it a placeholder. Running `uv run --env-file .env python scripts/capture_voyage_response.py` once replaces it with a real response. The tests read the token count from the file, so they still pass after the swap. The tests box stays open until then.
- **Batch size is 1.** Voyage reports `usage.total_tokens` per request, not per input, so a multi-text batch can't give each chunk "the API's token count". Each request carries one chunk, which is within the per-request limit (1,000 inputs, 120k tokens for voyage-4-large). The cost is about 2,144 sequential requests, with backoff on 429 and 5xx that honours `Retry-After`.
- **The length check is an approximation.** No Voyage tokenizer is available offline, so the check before any call compares each chunk's stored cl100k count against half of the 32k context (16,000). The largest chunk is 1,801. Truncation is off, so a chunk that passed the check and was still too long would make the API refuse it, not truncate it.
- **Cascade.** `chunk_embeddings.chunk_id` references `chunks` with `ON DELETE CASCADE`, because `load_filing` deletes and reinserts a filing's chunks. Rerunning `ingest.load` therefore drops that filing's vectors, and the next `ingest.embed` re-embeds them. Raised in docs/OPEN-DECISIONS.md.
- **Fake embedder.** The tests run the real `VoyageClient` over `httpx.MockTransport` replaying the response file, which exercises the HTTP code without a network.

**2026-10-05 (agent).** The user added `VOYAGE_API_KEY` and ran the capture script. `tests/fixtures/voyage/document_response.json` is now a real `voyage-4-large` response (1024 dimensions, 25 tokens, no credentials), and `tests/test_embed.py` passes on it (20 passed). All boxes are ticked. `python -m ingest.embed` has not been run yet.
