# Voyage response fixtures

`document_response.json` is a real `voyage-4-large` response, recorded on 2026-10-05 by
`scripts/capture_voyage_response.py`: one sentence embedded with `input_type: document` and
`truncation: false`, 1024 dimensions, `usage.total_tokens` 25. Only the response body is
stored; it holds no credentials. The tests replay it through `httpx.MockTransport`.

To record it again, with `VOYAGE_API_KEY` in `.env`:

    uv run --env-file .env python scripts/capture_voyage_response.py
