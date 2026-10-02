# FilingIntel

Citation-grounded Q&A over SEC 10-K and 10-Q filings from EDGAR. A portfolio project for a technical fintech audience: rigour and honest benchmarking matter more than feature count.
Design detail: docs/ARCHITECTURE.md (read when a task touches it). Milestones: docs/RUNBOOK.md. History and past findings: docs/BUILD-LOG.md.

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
Three tickers (NVDA, AMD, INTC), 10-K and 10-Q only. For new tickers, data sources or retrieval arms: propose, don't build.
EDGAR: descriptive User-Agent with contact details, rate limiting, cached downloads.

## Stack
Python 3.11+ (uv), FastAPI, LangGraph, Postgres + pgvector, Neo4j Community, Ragas, DeepEval, Phoenix, GitHub Actions, Streamlit. Decisions below are final unless I reopen them.

## Workflow
ECC commands in this install use the /everything-claude-code: prefix.
1. Plan with /everything-claude-code:plan and wait for approval. For major design decisions (ontology, entity resolution, research plan), run a grilling session with me first.
2. Write tests first (tdd-workflow skill) for deterministic logic, and show them failing.
3. Implement the minimal change.
4. Review code quality with /everything-claude-code:python-review, plus the database-reviewer agent for SQL or Cypher. Fix the findings.
5. Review spec compliance with /code-review (Matt Pocock skill) against the current RUNBOOK step, my latest instructions in this session, and docs/OPEN-DECISIONS.md. Fix every spec-compliance finding, or report why it can't be fixed. Never resolve a finding by weakening an acceptance check.
6. Verify with the verification-loop skill and the relevant eval slice.
7. Append a dated entry to docs/BUILD-LOG.md, ending with a "Next session starts with" note. Stop and report.
Use search-first before writing integration code for edgartools, LangGraph, Ragas, DeepEval or graph drivers.
Test fixtures are real downloaded filings (gzipped), never hand-written HTML.
Ask before: adding a dependency, changing a schema, changing the eval harness or CI, or anything outside the current milestone.

## Commands
- Tests: uv run pytest
- Lint: uv run ruff check .
- Databases: docker compose up -d
- (add when they exist: eval-fast, eval-full)

## Report format
Files changed; evidence (tests, eval results with commit); regressions; decisions needed, each with a recommendation; proposed next step.

## Decisions
- Package manager: uv, with uv.lock committed.
- Vector store: pgvector. Postgres also holds the chunk table and offsets.
- Graph: Neo4j Community via Docker. Kùzu rejected: upstream archived Oct 2025.
- Community detection: Leiden in Python (leidenalg or graspologic).
- Tracing: Phoenix.
- Tickers: NVDA, AMD, INTC. Two fiscal years of 10-K and 10-Q filings.
- Infra: one docker-compose.yml for Postgres and Neo4j, image tags pinned exactly. Credentials from env vars; .env.example committed, .env gitignored.
- EDGAR parser: edgartools 5.59.1. sec-parser rejected: no 10-K parser; failed completely on both INTC filings (spikes/parser_spike_report.txt).
- Parsed filings: data/parsed/{accession_no}.json. The text is built by concatenating section texts taken from the filing's markdown, in document order, with each section's char offsets recorded at construction. No search-based offset recovery. resolve(chunk_id) returns parsed_text[char_start:char_end]. Text before the first located section is kept as a `preamble` section, so offsets cover the whole filing. A required item may own several spans (Intel prints Item 7's Critical Accounting Estimates after Item 7A), found from the filer's cross-reference index.
- Section locating: item headings and the filer's cross-reference titles are the reference; an edgartools content anchor is used only if it agrees. Method per section is recorded as edgartools, heading or cross_reference_index.
- Corpus manifest: the two most recent complete fiscal years per ticker, each a 10-K plus the three 10-Qs with the same fiscal_period year (24 filings). A year is complete once its 10-K is filed.
- Financial statements: when Item 8 is only a pointer (NVIDIA: "set forth in" Item 15), part_iv_item_15 holds the statements. A required section under 2,000 chars is a failure unless it is this verified pointer case.
- Fiscal period: derive_fiscal_period(), confirmed against XBRL dei tags on all 6 spike filings.
- Embedding model: chosen in Step 5. Step 3 creates no vector column.
- CI makes no network calls; pytest-socket allows localhost only. Parser and chunker tests use the gzipped real filings in tests/fixtures/, rebuilt from data/raw by scripts/build_fixtures.py.
- docs/design/filingintel-demo.html is a layout reference only. Its tickers, question categories and numbers are placeholders; RUNBOOK is the source of truth.