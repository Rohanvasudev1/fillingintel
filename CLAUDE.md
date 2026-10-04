# FilingIntel

Citation-grounded Q&A over SEC 10-K and 10-Q filings from EDGAR. A portfolio project for a technical fintech audience: rigour and honest benchmarking matter more than feature count.
Design detail: docs/ARCHITECTURE.md (read when a task touches it). Milestones: docs/RUNBOOK.md. History and past findings: docs/BUILD-LOG.md.

## Invariants
The project's credibility rests on an honest benchmark. If an invariant blocks you, stop and tell me.
1. The eval set (`eval/eval_set.jsonl`) holds only human-written or human-verified records, each labelled with its provenance and author. Agent-drafted questions (allowed since 2026-10-04 at the user's request) live only in `eval/agent_drafted_set.jsonl`, labelled `agent_drafted`, and their results are always reported separately. A draft becomes `human_verified` only through the user's review (`python -m eval.review`). Agents never edit or relabel records in the human set, never write `eval/eval_set.jsonl` or `eval/review_log.jsonl`, and never run `python -m eval.review`, which also refuses non-interactive input: flag suspected errors instead.
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
5. Review spec compliance with /step-review (project skill adapted from Matt Pocock's code-review) against .claude/plans/step-<N>.md, the current RUNBOOK step and docs/OPEN-DECISIONS.md. Write any instructions I gave in chat into the plan file before the review. Fix every spec-compliance finding, or report why it can't be fixed. Never resolve a finding by weakening an acceptance check.
6. Verify with the verification-loop skill and the relevant eval slice.
7. Append a dated entry to docs/BUILD-LOG.md, ending with a "Next session starts with" note. Stop and report.
Apply the unslop skill to all prose: replies, docs, BUILD-LOG entries and commit messages.
Use search-first before writing integration code for edgartools, LangGraph, Ragas, DeepEval or graph drivers.
Test fixtures are real downloaded filings (gzipped), never hand-written HTML.
Ask before: adding a dependency, changing a schema, changing the eval harness or CI, or anything outside the current milestone.

## Commands
- Tests: uv run pytest
- Lint: uv run ruff check .
- Databases: docker compose up -d
- Tests with the database: uv run --env-file .env pytest
- Corpus (network; writes spikes/corpus_report.txt and data/parsed/): uv run --env-file .env python -m ingest.corpus
- Load and verify (offline; writes spikes/load_report.txt): uv run --env-file .env python -m ingest.load --verify
- Eval set (Step 4): uv run python -m eval.search <words> [--ticker] [--period] [--section] or --show <chunk_id>; eval.validate <file> [--kind] [--db]; eval.coverage <file>; eval.review --reviewer "<name>" (the user only); eval.merge_drafts
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
- Step 3 runs as three sub-steps, one session each: 3a table spans, 3b chunker, 3c Postgres load and resolve(). Plan: .claude/plans/step-3.md.
- Tables are located by their position in the parsed text (runs of `|` lines), not by matching edgartools tables by label. The chunk path does not use ParsedTable.section_key or doc.tables.
- Chunk tokens are counted with tiktoken cl100k_base until Step 5 picks the embedding model. Each chunk stores its tokenizer name.
- The preamble is chunked with section 'preamble'. Step 5 decides whether to embed it.
- Chunk IDs are deterministic ({accession_no}:{ordinal:04d}) and each chunk stores chunker_version. Chunker settings are frozen before Step 4 writes gold chunk IDs.
- Postgres schema for Step 3: a filings table (metadata, full parsed text, financial_statements_section) and a chunks table, from db/schema.sql. resolve() takes substr of the filing text; chunk text is not stored separately.
- ECC upgrade deferred until after Step 3, because it renames workflow commands.
- The cl100k_base encoding file is vendored in vendor/tiktoken/ (named by tiktoken's cache key) and tests/conftest.py sets TIKTOKEN_CACHE_DIR to it, so tests and CI never download it. tests/test_tokenizer_offline.py checks its hash and tokenizes with the network blocked.
- Step 3c pipeline: `python -m ingest.corpus` (network) writes data/parsed/{accession_no}.json; `python -m ingest.load [--verify]` (offline) chunks and loads Postgres from those files and writes spikes/load_report.txt. Database tests run in a throwaway schema per session and skip without DATABASE_URL; a guard test fails in CI if it is unset.
- Postgres schema (db/schema.sql) as approved, plus CHECKs on the chunk_id format, chunks.form_type and text_sha256 = sha256(parsed_text). apply_schema() fails with SchemaMismatch if live columns differ from the code. No sections table yet. Also accepted: the chunk_id CHECK pads with greatest(4, length) so ordinals over 9,999 are not truncated, and CHECKs on the accession_no pattern, filings.form_type, ordinal >= 0 and token_count >= 0.
- Provenance: git_state() reports the commit plus +dirty for tracked changes or untracked files under ingest/, db/, tests/ or scripts/.
- Eval set (Step 4, plan: .claude/plans/step-4.md):
  - Records follow eval/schema.py, with gold chunk text pinned by SHA-256.
  - Classes: lookup, local, multi_hop, global, plus decline and unanswerable (about 10 each).
  - 1–2 hard negatives per answerable question, labelled same_company_other_period, same_company_same_filing or peer_company. A wrong-evidence rate is reported per arm.
  - About 30% of records are held out as split "test", by a hash of the question text, and not looked at until the final benchmark.
- FinRank (CC BY-NC 4.0; pharmaceuticals, oil & gas and automotive) is not used as our question set, because it has no NVDA, AMD or INTC evidence. Its schema ideas are borrowed. Using its corpus as an external check is a proposal in OPEN-DECISIONS.
- Chunk coverage means every non-whitespace character of every section is in at least one chunk; whitespace between chunks and at section edges may be left out.
- CI makes no network calls; pytest-socket allows localhost only. Parser and chunker tests use the gzipped real filings in tests/fixtures/, rebuilt from data/raw by scripts/build_fixtures.py.
- docs/design/filingintel-demo.html is a layout reference only. Its tickers, question categories and numbers are placeholders; RUNBOOK is the source of truth.