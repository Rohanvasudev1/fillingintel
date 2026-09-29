# FilingIntel

Citation-grounded question answering over SEC filings. Ask what NVDA, AMD or INTC disclose in their 10-K and 10-Q filings, and every claim in the answer links to the exact passage it came from.

The project compares four retrieval strategies (vector search, graph neighbourhood search, Cypher traversal and community summaries) on one hand-written set of ~120 questions, and publishes where each one wins and loses.

**Status:** in progress. No benchmark results yet; they'll appear here once they come from real runs.

## Docs

- [Architecture](docs/ARCHITECTURE.md): system design
- [Runbook](docs/RUNBOOK.md): build order and milestones
- [Graph layer](docs/GRAPH-LAYER.md): ontology, entity resolution and graph retrieval design

## Stack

Python, FastAPI, LangGraph, Postgres with pgvector, Neo4j, Ragas, DeepEval, Phoenix, GitHub Actions, Streamlit.

## Scope

Three companies, 10-K and 10-Q filings only. This is a research and engineering project; it does not give investment advice.