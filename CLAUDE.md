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
2. `/research` for every library, API or outside technique the step touches (always for edgartools, LangGraph, Ragas and graph drivers). Start it in the background during the grilling, so its findings can change later rounds; decisions that depend on it wait for it. Notes go in docs/research/ and the spec cites them. A step with nothing external says so in the spec.
3. `/to-spec` into `.scratch/step-<N>/spec.md`, then `/to-tickets` into `.scratch/step-<N>/issues/`. Write any instructions I gave in chat into the spec. Wait for my approval before implementing. Keep steps 1–3 in one context window.
4. `/implement` one ticket at a time, clearing context between tickets. It drives `/tdd`: for deterministic logic, show each test failing before writing the code.
5. Matt's code review before each commit: invoke `mattpocock-skills:code-review` by that full name, including when `/implement` says `/code-review`. Never use Claude Code's built-in `/code-review`, which has the same short name but reviews only for bugs, with no Standards or Spec axis. The Spec axis checks the ticket, the step spec, the RUNBOOK step and docs/OPEN-DECISIONS.md. For SQL or Cypher, the Standards axis also checks injection safety, indexes and constraints. Fix every finding, or report why it can't be fixed. Never resolve a finding by weakening an acceptance check.
6. Verify: `uv run ruff check .`, `uv run --env-file .env pytest`, and the relevant eval slice.
7. Append a dated entry to docs/BUILD-LOG.md, ending with a "Next session starts with" note. Stop and report.
Hard bugs go through `/diagnosing-bugs`.
Apply the unslop skill to all prose: replies, docs, BUILD-LOG entries and commit messages.
Records stay as before: RUNBOOK defines the steps, BUILD-LOG gets an entry per session, OPEN-DECISIONS holds unsettled questions, and Decisions below holds settled ones. The plans in .claude/plans/ (Steps 3–4) are history; .claude/skills/step-review is kept but no longer used.
Test fixtures are real downloaded filings (gzipped), never hand-written HTML.
Ask before: adding a dependency, changing a schema, changing the eval harness or CI, or anything outside the current milestone.

## Commands
- Tests: uv run pytest
- Lint: uv run ruff check .
- Databases and Phoenix: docker compose up -d
- Phoenix UI: http://localhost:6006
- Tests with the database: uv run --env-file .env pytest
- Corpus (network; writes spikes/corpus_report.txt and data/parsed/): uv run --env-file .env python -m ingest.corpus
- Load and verify (offline; writes spikes/load_report.txt): uv run --env-file .env python -m ingest.load --verify
- Eval set (Step 4): uv run python -m eval.search <words> [--ticker] [--period] [--section] or --show <chunk_id>; eval.validate <file> [--kind] [--db]; eval.coverage <file>; eval.review --reviewer "<name>" (the user only); eval.merge_drafts
- Quality gate (offline, needs only DATABASE_URL; loads the snapshot into a throwaway schema): uv run --env-file .env python -m eval.gate. Exit 0 pass, 2 bad input, 3 could not run, 4 scores dropped. `--update-baseline` rewrites benchmarks/gate_baseline.json; never lower it without the user's yes.
- Graph validation (reads NEO4J_URI/USER/PASSWORD; adds the Postgres chunk check when DATABASE_URL is set): uv run --env-file .env python -m graph.validate. Exit 0 valid, 2 bad arguments, 3 could not run, 4 violations found.
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
  - claude-sonnet-5-5 writes answers at effort `high`; it rejects non-default temperature, top_p and top_k, so sampling stays at the defaults (docs/research/ragas-deepeval-claude-judge.md). Judged metrics carry the label "uncalibrated" until judge calibration.
  - Judge (user decision 2026-10-05, Step 5 spec amendment): gpt-6-luna at effort `medium` through OpenAI's Responses API, with a strict json_schema format, store=false, max_output_tokens 10,000 and no sampling settings (docs/research/openai-luna-judge.md). The ticket 10 spot check against gpt-6-sol (commit 6ac5e1f) kept effort `medium`; the cap was lowered from 25,000 after it (largest luna reply 2,877 tokens). The Opus judge path is deleted. Config pins provider, model, effort and output cap, pyproject pins the openai SDK, and each run records all five. `openai==3.3.0` is a direct dependency.
  - A cache keyed by model, effort and prompt hash stores every API response, so reruns reproduce answers. Ticket 08 adds one uncached repeat dev run to report run-to-run variance.
  - Approved dependencies: anthropic, ragas==0.4.3 and (2026-10-05) openai==3.3.0. Voyage calls go through httpx. LangGraph waits for Step 13.
  - DeepEval dropped (user decision, 2026-10-05): it reads .env at import, registers a pytest plugin, passes unsupported claims by default and costs 3 to 4 judge calls per answer. Judge calibration is the second opinion instead.
  - Ragas runs through a project judge class (eval/judging/openai_judge.py) on OpenAI's strict structured outputs, because Ragas's documented routes send sampling parameters and, for OpenAI, misread model versions. RAGAS_DO_NOT_TRACK=true, and a test imports ragas with the network blocked.
- Step 5 harness (grilling, 2026-10-04):
  - eval.run checks VOYAGE_API_KEY, ANTHROPIC_API_KEY and OPENAI_API_KEY at start, and refuses to start when OPENAI_BASE_URL is set. pytest stays offline and replays recorded API responses.
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
- Step 5 judges (ticket 07, user decisions 2026-10-05): `pyproject.toml` caps langchain-community below 0.4.2 with a uv constraint, because ragas 0.4.3 imports a module 0.4.2 removed. Ragas faithfulness scores the kept sentences a reader sees, not the raw answer; dropped sentences are counted in dropped_share.
- docs/design/filingintel-demo.html is a layout reference only. Its tickers, question categories and numbers are placeholders; RUNBOOK is the source of truth.
- ADR-0003 (docs/adr/0003-offline-retrieval-quality-gate.md): the quality gate runs the vector arm's real search offline over a committed snapshot and scores retrieval only, no API calls.
- Step 6 (grilling, 2026-10-06):
  - Gate questions: the 82 answerable `dev` questions (not the RUNBOOK's ~40); decline, unanswerable and `test` never run in CI.
  - Gated numbers: recall@5, recall@10 and wrong-evidence rate, per class and pooled. Zero tolerance: any drop fails.
  - Gate baseline: benchmarks/gate_baseline.json, written by `python -m eval.gate --update-baseline`, with commit, snapshot hash and question set hash. A test checks its first version against the committed baseline run. Agents never lower it to pass a PR; a lowering needs the user's yes and a BUILD-LOG line.
  - Snapshot: one gzipped file of the real filings, chunks and chunk_embeddings rows plus the gated query vectors, built by scripts/build_fixtures.py from the local database and checked against it when present. No schema change.
  - Workflow: one branch and PR per ticket; `main` protected with the gate as a required check (enabled only after the user approves the settings change).
  - Phoenix: a pinned service in docker-compose.yml, hand-written OpenTelemetry spans with OpenInference names (filter, query embed, vector search, generation, each judge call). Attributes: model, tokens, latency, cost, cache hit, chunk IDs with scores, question text; prompt and answer text off unless a setting turns them on; never keys or headers. Tracing is off unless PHOENIX_COLLECTOR_ENDPOINT is set. `/research` before integration code.
  - `benchmarks/results.md` deferred (OPEN-DECISIONS).
- Step 6 tracing and repo settings (grilling round 3, 2026-10-06; research: docs/research/phoenix-tracing.md):
  - Approved dependencies: opentelemetry-sdk==1.45.0, opentelemetry-exporter-otlp-proto-http==1.45.0 (OTLP over HTTP, no grpcio) and openinference-semantic-conventions==0.1.41. arize-phoenix-otel rejected: it pulls grpcio, exports to localhost:4317 with no endpoint set, and reads `.env.phoenix` files.
  - Compose: arizephoenix/phoenix:20.19.0 with a data volume (PHOENIX_WORKING_DIR), PHOENIX_TELEMETRY_ENABLED=false and PHOENIX_ALLOW_EXTERNAL_RESOURCES=false. Phoenix, Postgres and Neo4j ports bind to 127.0.0.1 only.
  - Tracing off means no SDK objects are built. BatchSpanProcessor with a 2 s exporter timeout and an explicit shutdown. Tests pass their own TracerProvider with an in-memory exporter; code never calls trace.set_tracer_provider. An autouse pytest fixture removes PHOENIX_COLLECTOR_ENDPOINT and OTEL_EXPORTER_OTLP_*.
  - Cost in traces: Phoenix prices LLM spans from token counts, so cache replays carry no token counts, Claude's prompt count is uncached + cache-read + cache-write, and Voyage and per-call costs go in project attributes.
  - Text-capture setting (user decision, 2026-10-06): `FILINGINTEL_TRACE_TEXT`, off unless `true`. Only then do prompt and answer text go on spans.
  - eval/judging/runner.py submits judge tasks with contextvars.copy_context().run so judge spans keep their parent. Scores do not change.
  - `main` is protected by a ruleset requiring the gate job, with no bypass; changing it means editing the ruleset (set up only after the user approves). CI triggers on pull_request and on push to main, with no paths filters.
- Step 6 confirmations (user decision, 2026-10-07):
  - `eval.gate` loads the committed snapshot itself, into a throwaway schema it drops afterwards, so the laptop and CI run the same command.
  - The gate's question set hash covers only the 82 gated records; editing a decline or `test` record does not force a baseline rewrite.
  - Judge prompt text never goes on spans, even with `FILINGINTEL_TRACE_TEXT=true`; that setting covers the answer prompt and answer only.
  - Ruleset 24576386 also requires a pull request (0 approvals, since the user can't approve their own PRs), so nothing reaches `main` except through a PR with both checks green.
- ADR-0004 (docs/adr/0004-graph-evidence-on-edges-and-per-filing-nodes.md): every extracted edge stores `chunk_ids` and `evidence_spans` as parallel lists, and extracted nodes keep `EVIDENCED_BY` edges to Chunk nodes. RiskFactor and MetricValue are nodes per filing; other extracted labels are one node across filings. Rewriting an edge adds evidence, never a parallel edge.
- Step 7 (grilling, 2026-10-07; research: docs/research/neo4j-driver-and-constraints.md):
  - Scope: the ontology module, constraints, `validate_graph()` and a minimal guarded write path, in a new `graph/` package (ontology, schema_text, constraints, write, validate). The Postgres-to-graph loader for structural nodes waits for Step 10.
  - Labels. Structural (from EDGAR metadata, evidenced by the filing record): Company (the three filers), Filing, Period, Chunk. Extracted (at least one chunk as evidence): Organization, Segment, Product, RiskFactor, Regulation, LegalProceeding, Agreement, Facility, Person, MetricValue. Subsidiary is an Organization with `OWNS`. Community is added in Step 12 as a new ontology version.
  - Supplier, customer and competitor are edges (`SUPPLIES`, `CUSTOMER_OF`, `COMPETES_WITH`) between Company and Organization nodes, not node types.
  - Edge types (start → end): FILED Company→Filing; COVERS_PERIOD Filing→Period; PART_OF Chunk→Filing; EVIDENCED_BY extracted node→Chunk; HAS_SEGMENT Company→Segment; OFFERS Company|Segment→Product; DISCLOSES Filing→RiskFactor; AFFECTS RiskFactor→Segment|Product; PERSISTS_AS RiskFactor→RiskFactor; REPORTS Filing→MetricValue; MEASURES MetricValue→Company|Segment|Product; SUPPLIES, CUSTOMER_OF, COMPETES_WITH Company|Organization→Company|Organization (COMPETES_WITH stored one way, queried both); OWNS Company|Organization→Organization|Facility; PARTY_TO Company|Organization→Agreement; CONCERNS Agreement→Organization|Product|Facility; SUBJECT_TO Company|Product→Regulation; INVOLVED_IN Company|Organization→LegalProceeding (role); HOLDS_ROLE_AT Person→Company (role). Any other endpoint pair is rejected.
  - Chunk nodes store chunk_id, accession_no and section, never text; text comes from resolve() in Postgres.
  - Keys: uniqueness on Company.cik, Filing.accession_no, Chunk.chunk_id, Period (cik, fiscal_period), and a `key` on each extracted label. How extracted keys are built is decided in Steps 8–9.
  - Required properties: every node and edge has `ontology_version`; extracted edges have `chunk_ids` and `evidence_spans`; EVIDENCED_BY has `evidence_span`. `confidence` and the extract prompt version come in Step 8 with a version bump.
  - Versioning: an `ONTOLOGY_VERSION` integer and the SHA-256 of the generated schema text, stored on one `:GraphMeta` node. The schema text (labels, properties, edge endpoints, hand-written one-line descriptions) is generated from the module in a fixed order, never hand-maintained.
  - apply_constraints() creates constraints with IF NOT EXISTS, then compares `SHOW CONSTRAINTS` with the code and fails with ConstraintMismatch on a missing or extra one (IF NOT EXISTS silently keeps an old definition under the same name). It refuses when `:GraphMeta` holds another ontology version.
  - Write path: a batch is checked in Python against the ontology and evidence rules before any database call; any violation writes nothing and lists every violation; valid batches are one transaction. Labels and edge types come from the ontology allowlist in backticks, never from `$(...)` dynamic labels (which scan every node on MATCH).
  - validate_graph() checks labels and edge types, endpoints, required properties and types, evidence (extracted nodes have EVIDENCED_BY; extracted edges have non-empty chunk_ids that are existing Chunk nodes, with spans of the same length), structural shape (one FILED and one COVERS_PERIOD per Filing, one PART_OF per Chunk), `:GraphMeta`, and, when DATABASE_URL is set, that each Chunk node's chunk_id exists in Postgres under the same filing. It collects every violation and raises one error with counts and examples; `python -m graph.validate` has distinct exit codes. Verbatim-span checks stay in Step 8. It is not a CI gate until Step 13.
  - Tests: hand-built graphs around real chunk IDs from the fixture filings, quoting real spans; each bad case breaks one thing. A pinned Neo4j service runs in CI; tests skip without NEO4J_TEST_URI and a guard test fails in CI if it is unset. Tests run only against a database holding a test marker node; locally a separate `neo4j-test` compose service on 127.0.0.1:7688.
  - Property types (ticket 01, user decision 2026-10-07): Filing's date is `filing_date` (DATE), named as in Postgres; `MetricValue.value` is FLOAT, and the write path accepts an int and stores it as a float; `OWNS.stake` is FLOAT, the owned share from 0 to 1; keys, `cik` and `fiscal_period` are STRING. Ticket 03 adds `graph/` to git_state()'s +dirty list.
  - Approved dependency: `neo4j==6.3.1` (pulls only pytz), created with `telemetry_disabled=True` and an explicit database name.
  - Glossary: "edge" in docs and code, "relationship" only when quoting Neo4j; the extractor's output is a "candidate triple" until validated.
  - Rewrites (ticket 03, user decision 2026-10-07): a node or edge written again takes the latest batch's property values; an `EVIDENCED_BY` edge keeps its first span. Revisit in Step 8 with `confidence` (OPEN-DECISIONS).
  - Validator details (tickets 04–05): `validate_graph()` has no exclusion for the test marker; validation tests remove it and put it back. Its read session turns off Neo4j's UNRECOGNIZED ("does not exist") notifications only, since its checks name labels a young graph lacks; other notification classes still log.
