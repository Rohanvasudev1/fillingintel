# Voyage response fixtures

`document_response.json` is a **placeholder**, not a recorded API response. It follows the
response shape in Voyage's embeddings API reference (https://docs.voyageai.com/reference/embeddings-api):
one `voyage-4-large` embedding of 1024 seeded random floats (seed 20261005, unit norm) and
`usage.total_tokens` 412. No Voyage key was available when Step 5 ticket 02 was built.

To replace it with a real response, run once with a key in `.env`:

    uv run --env-file .env python scripts/capture_voyage_response.py

The script embeds one fixed sentence with `input_type: document` and `truncation: false` and
writes only the response body, which carries no credentials. Then delete this placeholder note.
