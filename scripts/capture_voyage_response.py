"""Record one real Voyage response as tests/fixtures/voyage/document_response.json.

Needs ``VOYAGE_API_KEY`` and the network; costs a few tokens.  The request is
sent by ``VoyageClient`` itself, so the fixture matches what the code sends.
Only the response body is written; it holds no credentials.

    uv run --env-file .env python scripts/capture_voyage_response.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.voyage import (  # noqa: E402
    DEFAULT_MODEL,
    REQUEST_TIMEOUT_SECONDS,
    MissingApiKey,
    VoyageClient,
    VoyageError,
)

OUT = Path(__file__).resolve().parents[1] / "tests/fixtures/voyage/document_response.json"
TEXT = "Revenue for fiscal year 2025 was $26.0 billion, up 2% from a year ago."


def main() -> int:
    bodies: list[bytes] = []

    def record(response: httpx.Response) -> None:
        response.read()
        if response.status_code == 200:
            bodies.append(response.content)

    http = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, event_hooks={"response": [record]})
    try:
        client = VoyageClient.from_env(http=http)
    except MissingApiKey as exc:
        http.close()
        print(str(exc), file=sys.stderr)
        return 2
    with client:
        try:
            client.embed_document(TEXT, DEFAULT_MODEL)  # raises if the response is malformed
        except VoyageError as exc:
            print(f"Voyage call failed: {exc}", file=sys.stderr)
            return 1
    body = json.loads(bodies[-1])
    OUT.write_text(json.dumps(body) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name}: {body['usage']['total_tokens']} tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())
