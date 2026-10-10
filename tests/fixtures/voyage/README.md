# Voyage response fixtures

`document_response.json` is a real `voyage-4-large` response, recorded on 2026-10-05 by
`scripts/capture_voyage_response.py`: one sentence embedded with `input_type: document` and
`truncation: false`, 1024 dimensions, `usage.total_tokens` 25. Only the response body is
stored; it holds no credentials. The tests replay it through `httpx.MockTransport`.

To record it again, with `VOYAGE_API_KEY` in `.env`:

    uv run --env-file .env python scripts/capture_voyage_response.py

`entity_vectors.json.gz` holds real voyage-4-large vectors (input type
`document`) for 17 "Label: name" texts from NVDA run 1, copied from the entity
embedding cache after `python -m resolve.run` on 2026-10-10 (Step 9, ticket 02).
It holds texts and vectors only, no credentials.
