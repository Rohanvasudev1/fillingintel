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
Python 3.11+ (uv), FastAPI, LangGraph, Postgres + pgvector, Neo4j Community, Ragas, Phoenix, GitHub Actions, Streamlit. Decisions below are final unless I reopen them.

## Workflow
Matt Pocock's skills (plugin `mattpocock-skills`, official marketplace) run the workflow since 2026-10-04. Run `/ask-matt` when unsure which flow fits a task. Each RUNBOOK step follows Matt's main flow:
1. `/grill-with-docs` with me. It records the vocabulary in CONTEXT.md and hard-to-reverse decisions as ADRs in docs/adr/. Add a one-line pointer to each new ADR under Decisions below.
2. `/to-spec` into `.scratch/step-<N>/spec.md`, then `/to-tickets` into `.scratch/step-<N>/issues/`. Write any instructions I gave in chat into the spec. Wait for my approval before implementing. Keep steps 1–2 in one context window.
3. `/implement` one ticket at a time, clearing context between tickets. It drives `/tdd`: for deterministic logic, show each test failing before writing the code.
4. Matt's code review before each commit: invoke `mattpocock-skills:code-review` by that full name, including when `/implement` says `/code-review`. Never use Claude Code's built-in `/code-review`, which has the same short name but reviews only for bugs, with no Standards or Spec axis. The Spec axis checks the ticket, the step spec, the RUNBOOK step and docs/OPEN-DECISIONS.md. For SQL or Cypher, the Standards axis also checks injection safety, indexes and constraints. Fix every finding, or report why it can't be fixed. Never resolve a finding by weakening an acceptance check.
5. Verify: `uv run ruff check .`, `uv run --env-file .env pytest`, and the relevant eval slice.
6. Append a dated entry to docs/BUILD-LOG.md, ending with a "Next session starts with" note. Stop and report.
Hard bugs go through `/diagnosing-bugs`. Use `/research` before writing integration code for edgartools, LangGraph, Ragas or graph drivers; its notes go in docs/research/.
Apply the unslop skill to all prose: replies, docs, BUILD-LOG entries and commit messages.
Records stay as before: RUNBOOK defines the steps, BUILD-LOG gets an entry per session, OPEN-DECISIONS holds unsettled questions, and Decisions below holds settled ones. The plans in .claude/plans/ (Steps 3–4) are history; .claude/skills/step-review is kept but no longer used.
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

## Agent skills

### Issue tracker
Local markdown under `.scratch/<feature>/`, committed to git. See `docs/agents/issue-tracker.md`.

### Triage labels
The five default roles (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs
Single-context: CONTEXT.md and docs/adr/ at the repo root. See `docs/agents/domain.md`.

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
- Embedding model: voyage-4-large, chosen in Step 5. See ADR-0002.
- Step 3 runs as three sub-steps, one session each: 3a table spans, 3b chunker, 3c Postgres load and resolve(). Plan: .claude/plans/step-3.md.
- Tables are located by their position in the parsed text (runs of `|` lines), not by matching edgartools tables by label. The chunk path does not use ParsedTable.section_key or doc.tables.
- The chunker counts tokens with tiktoken cl100k_base, and each chunk stores its tokenizer name. chunk_embeddings also stores the token count Voyage reports.
- The preamble is chunked with section 'preamble' and embedded like any other chunk (ADR-0002).
- Chunk IDs are deterministic ({accession_no}:{ordinal:04d}) and each chunk stores chunker_version. Chunker settings are frozen before Step 4 writes gold chunk IDs.
- Postgres schema for Step 3: a filings table (metadata, full parsed text, financial_statements_section) and a chunks table, from db/schema.sql. resolve() takes substr of the filing text; chunk text is not stored separately.
- ECC removed on 2026-10-04 (user decision): ECC 2.2.3 added about 45k tokens to every session and bundled an npx MCP server. Matt Pocock's skills plugin replaces it (about 1.6k tokens). Old skill copies are backed up in ~/.claude/backup-ecc-1.4.1/.
- The cl100k_base encoding file is vendored in vendor/tiktoken/ (named by tiktoken's cache key) and tests/conftest.py sets TIKTOKEN_CACHE_DIR to it, so tests and CI never download it. tests/test_tokenizer_offline.py checks its hash and tokenizes with the network blocked.
- Step 3c pipeline: `python -m ingest.corpus` (network) writes data/parsed/{accession_no}.json; `python -m ingest.load [--verify]` (offline) chunks and loads Postgres from those files and writes spikes/load_report.txt. Database tests run in a throwaway schema per session and skip without DATABASE_URL; a guard test fails in CI if it is unset.
- Postgres schema (db/schema.sql) as approved, plus CHECKs on the chunk_id format, chunks.form_type and text_sha256 = sha256(parsed_text). apply_schema() fails with SchemaMismatch if live columns differ from the code. No sections table yet. Also accepted: the chunk_id CHECK pads with greatest(4, length) so ordinals over 9,999 are not truncated, and CHECKs on the accession_no pattern, filings.form_type, ordinal >= 0 and token_count >= 0.
- Provenance: git_state() reports the commit plus +dirty for tracked changes or untracked files under ingest/, db/, eval/, retrieve/, prompts/, tests/ or scripts/ (eval/, retrieve/ and prompts/ added 2026-10-05, user decision).
- Eval set used for the benchmark (user decision, 2026-10-04): the 140 agent-drafted questions in eval/agent_drafted_set.jsonl, without human review. Every result on them is labelled "agent-drafted questions", never presented as human-written or human-verified. eval/eval_set.jsonl stays empty unless the user writes or reviews questions later; any such human records are reported in their own column.
- Eval set (Step 4, plan: .claude/plans/step-4.md):
  - Records follow eval/schema.py, with gold chunk text pinned by SHA-256.
  - Classes: lookup, local, multi_hop, global, plus decline and unanswerable (about 10 each).
  - 1–2 hard negatives per answerable question, labelled same_company_other_period, same_company_same_filing or peer_company. A wrong-evidence rate is reported per arm.
  - About 30% of records are held out as split "test", by a hash of the question text, and not looked at until the final benchmark.
- FinRank (CC BY-NC 4.0; pharmaceuticals, oil & gas and automotive) is not used as our question set, because it has no NVDA, AMD or INTC evidence. Its schema ideas are borrowed. Its pooled corpus is used as an external check of the vector arm after the baseline, reported as an appendix (user decision, 2026-10-04). It is the only data source outside EDGAR.
- Research plan: docs/RESEARCH-PLAN.md, approved 2026-10-04. Its hypotheses, thresholds and class-to-arm map are fixed; changes need a dated amendment in that file. Classes with fewer than 10 test questions are reported as "directional". The 20 decline and unanswerable questions sit outside the RUNBOOK's 120.
- Chunk coverage means every non-whitespace character of every section is in at least one chunk; whitespace between chunks and at section edges may be left out.
- CI makes no network calls; pytest-socket allows localhost only. Parser and chunker tests use the gzipped real filings in tests/fixtures/, rebuilt from data/raw by scripts/build_fixtures.py.
- tests/fixtures/corpus_filings.json holds the 24 corpus filings' metadata only (no text), generated from data/parsed by scripts/build_fixtures.py, so the question filter tests run in CI. A test checks it against data/parsed when present. It is derived data, not a hand-written fixture.
- ADR-0001 (docs/adr/0001-question-filters-from-question-text.md): the parser reads company, period and form filters from the question text, never from eval labels. Refinements of 2026-10-05: periods outside the corpus are ignored, a fourth quarter or a shared-word period list sets no period, and an empty selection means no filter.
- ADR-0002 (docs/adr/0002-chunk-embeddings-table-exact-search.md): voyage-4-large embeddings go in a separate chunk_embeddings table. Search is exact, truncation is off, and the preamble is embedded.
- Step 5 models and scope (grilling, 2026-10-04):
  - Step 5 scores `dev` only. Scoring `test` needs an explicit `--final`.
  - claude-sonnet-5-5 writes answers at effort `high` and claude-opus-5-5 judges at effort `medium`. Both models reject non-default temperature, top_p and top_k, so sampling stays at the defaults (docs/research/ragas-deepeval-claude-judge.md). Config pins both IDs and efforts, and each run records them. Judged metrics carry the label "uncalibrated" until judge calibration.
  - A cache keyed by model, effort and prompt hash stores every API response, so reruns reproduce answers. Ticket 08 adds one uncached repeat dev run to report run-to-run variance.
  - Approved dependencies: anthropic and ragas==0.4.3. Voyage calls go through httpx. LangGraph waits for Step 13.
  - DeepEval dropped (user decision, 2026-10-05): it reads .env at import, registers a pytest plugin, passes unsupported claims by default and costs 3 to 4 judge calls per answer. Judge calibration is the second opinion instead.
  - Ragas runs through a project judge class on Claude's structured outputs, because its documented Anthropic route sends sampling parameters Opus 5.5 rejects. RAGAS_DO_NOT_TRACK=true, and a test imports ragas with the network blocked.
- Step 5 harness (grilling, 2026-10-04):
  - eval.run checks VOYAGE_API_KEY and ANTHROPIC_API_KEY at start. pytest stays offline and replays recorded API responses.
  - `python -m ingest.embed` needs the network and embeds the chunks. A disk cache keyed by model and text hash holds query embeddings.
  - The vector arm retrieves the top 10 and passes all 10 to the answer model. Metrics are scored at 5 and 10.
  - Each answer sentence cites `[chunk_id]`. A deterministic step drops and counts sentences with no citation or with a citation outside the retrieved chunks. Citation validity has a deterministic structural part and a judged support part.
  - The answer prompt declines advice and says when the filings lack the evidence. A judge scores both behaviours, and they get their own rows until the router exists in Step 13.
  - Context recall, precision and the wrong-evidence rate come from chunk IDs, with no LLM. Ragas gives faithfulness and relevancy.
  - The answer model runs once per question. Each judge runs 3 times, and the report shows the mean and spread. Bootstrap 95% intervals use a fixed seed.
  - Each run writes benchmarks/runs/{date}-{arm}-{split}-{commit}.json with a provenance header, cells keyed by arm and class, and per-question records. Cost comes from a dated price table in config. Only the baseline run goes into git.
  - The control arm gives multi-company questions a plain top 10. A per-company quota would be a separate, labelled ablation.
  - The answer prompt lives in prompts/answer/v1.md. Any edit creates a new version. The run header records the prompt version and hash, and the Ragas version.
- Step 5 embeddings (ticket 02, user decision 2026-10-05):
  - `python -m ingest.embed` sends one chunk per Voyage request, so each row stores the token count Voyage reports for that chunk.
  - `chunk_embeddings` references `chunks` with ON DELETE CASCADE. Rerunning `ingest.load` drops the vectors of reloaded filings, and the next `ingest.embed` re-embeds them. A later change may make `load_filing` skip unchanged filings.
- Answer statuses and invariant 3 (user decision, 2026-10-05): the answer prompt asks for a status line (answered, declined, not_found). A decline is shown as a fixed sentence saying FilingIntel gives no investment advice; a not-found answer as a fixed sentence saying the retrieved filings lack the evidence, followed by any cited sentences that survived. These two fixed sentences are system statements, not claims about the filings, so they carry no citation. Every other sentence must cite a retrieved chunk or is dropped.
- Wrong evidence (user decision, 2026-10-05): a hard negative counts when it ranks above at least one gold chunk (an unretrieved gold chunk ranks last) or is cited anywhere in the raw answer. Recorded as an amendment in docs/RESEARCH-PLAN.md.
- docs/design/filingintel-demo.html is a layout reference only. Its tickers, question categories and numbers are placeholders; RUNBOOK is the source of truth.