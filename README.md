# FilingIntel

Citation-grounded question answering over SEC filings. Ask what NVDA, AMD or INTC disclose in their 10-K and 10-Q filings, and every claim in the answer links to the exact passage it came from.

The project compares four retrieval strategies (vector search, graph neighbourhood search, Cypher traversal and community summaries) on one hand-written set of ~120 questions, and publishes where each one wins and loses.

**Status:** in progress. Ingest, chunking and `resolve(chunk_id)` are built and verified on 24 filings (RUNBOOK Steps 2–3). Step 4 tooling is in place, with 140 agent-drafted candidate questions labelled as such and awaiting human review. The human-written or human-verified eval set is not complete yet. No benchmark results yet; they'll appear here once they come from real runs.

## Docs

- [Architecture](docs/ARCHITECTURE.md): system design
- [Runbook](docs/RUNBOOK.md): build order and milestones
- [Graph layer](docs/GRAPH-LAYER.md): ontology, entity resolution and graph retrieval design

## Running it

Needs Docker, [uv](https://docs.astral.sh/uv/) and a `.env` made from `.env.example`. `EDGAR_USER_AGENT` must hold a real name and email, as the SEC requires.

```bash
docker compose up -d                                          # Postgres and Neo4j
uv run --env-file .env python -m ingest.corpus                # download, parse, report; writes data/parsed/
uv run --env-file .env python -m ingest.load --verify         # load Postgres, check resolve() on every chunk
uv run --env-file .env pytest                                 # tests (database tests need DATABASE_URL)
```

## Stack

Python, FastAPI, LangGraph, Postgres with pgvector, Neo4j, Ragas, DeepEval, Phoenix, GitHub Actions, Streamlit.

## Scope

Three companies, 10-K and 10-Q filings only. This is a research and engineering project; it does not give investment advice.