# Anthropic response fixtures

Three answer-model responses for dev questions, one per answer status:

- `answered_q0072.json`: a cited answer (q0072, lookup)
- `declined_q0005.json`: an investment-advice decline (q0005)
- `not_found_q0011.json`: the filings lack the evidence (q0011, unanswerable)

Each file holds the question, the 10 retrieved chunk IDs and the response body. The
`recorded` field says where the body came from.

**Status, 2026-10-05: placeholders.** No `ANTHROPIC_API_KEY` was set, so the bodies were
hand-built in the Messages API response shape and validated with the SDK's `Message`
model. They are not API responses. To replace them with real responses, with
`DATABASE_URL`, `VOYAGE_API_KEY` and `ANTHROPIC_API_KEY` in `.env`:

    uv run --env-file .env python scripts/capture_anthropic_responses.py

The script calls the API directly, bypassing the response cache, and writes only the
response bodies. They hold no credentials. The tests replay them through a fake SDK client
and a fake answer model.
