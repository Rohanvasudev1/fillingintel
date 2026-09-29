# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# FilingIntel

Citation-grounded Q&A over SEC 10-K and 10-Q filings from EDGAR. A portfolio project for a technical fintech audience: rigour and honest benchmarking matter more than feature count.
Design detail: docs/ARCHITECTURE.md (read when a task touches it). Milestones: docs/RUNBOOK.md.

## Invariants
The project's credibility rests on an honest benchmark. If an invariant blocks you, stop and tell me.
1. The eval set is human-authored. Never create, edit or relabel questions or gold answers. Flag suspected errors instead.
2. Report only benchmark numbers produced by running the harness, with the commit and config. Label any placeholder.
3. Every claim in an answer cites a real chunk ID. Uncited sentences are dropped.
4. Every graph edge stores the source chunk it came from. Edges without one are rejected at load.
5. Never lower CI thresholds, skip categories or mark tests xfail to pass the gate. Report regressions.
6. No investment advice (buy/hold/sell, price targets). The router declines these.
7. Config comes from environment variables. Never read .env or log secrets.

## Scope
2–3 tickers, 10-K and 10-Q only. For new tickers, data sources or retrieval arms: propose, don't build.
EDGAR: descriptive User-Agent with contact details, rate limiting, cached downloads.

## Stack
Python 3.11+ (uv), FastAPI, LangGraph, Postgres + pgvector, Neo4j Community, Ragas, DeepEval, Phoenix, GitHub Actions, Streamlit. Decisions below are final unless I reopen them.


## Workflow
Plan with /ecc:plan and wait for approval. Write tests first (tdd-workflow) for deterministic logic. Implement the minimal change. Review with /python-review, plus database-reviewer for SQL or Cypher. Verify with /quality-gate and the relevant eval slice. Stop at the milestone boundary and report.
Use search-first before writing integration code for LangGraph, Ragas, DeepEval or graph drivers.
Ask before: adding a dependency, changing a schema, changing the eval harness or CI, or anything outside the current milestone.

## Commands
(add once they exist: test, lint, eval-fast, eval-full)

## Report format
Files changed; evidence (tests, eval results with commit); regressions; decisions needed, each with a recommendation; proposed next step.

## Decisions
- Package manager: uv, with uv.lock committed.
- Vector store: pgvector. Postgres also holds the chunk table and offsets.
- Graph: Neo4j Community via Docker. Kùzu rejected: upstream archived Oct 2025.
- Community detection: Leiden in Python (leidenalg or graspologic).
- Tracing: Phoenix.
- Tickers: NVDA, AMD, INTC. Two fiscal years of 10-K and 10-Q filings.
- Infra: one docker-compose.yml for Postgres and Neo4j. Credentials from env vars; .env.example committed, .env gitignored.
- Parsed filings: data/parsed/{accession_no}.json holds the full parsed text plus a list of sections with char offsets. Chunk offsets index into this parsed text; resolve(chunk_id) returns parsed_text[char_start:char_end].
- EDGAR parser: chosen by a timeboxed spike in Step 2 (edgartools vs sec-parser, one filing per ticker; pick whichever gets Item boundaries right). Record the result here.
- Embedding model: chosen in Step 5. Step 3 creates no vector column.
- CI makes no network calls. Parser and chunker tests use small fixture files in tests/fixtures/.
- docs/design/filingintel-demo.html is a layout reference only. Its tickers, question categories and numbers are placeholders; RUNBOOK is the source of truth.
