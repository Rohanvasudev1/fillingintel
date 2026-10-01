# FilingIntel — Build log

A dated record of each checkpoint: what was built, the evidence, what went wrong, and what was decided. Newest entries at the bottom.

This log is the raw material for the final README ("what I'd do differently"), for interviews, and for write-ups. Record problems and reversals as faithfully as wins. Numbers here come only from real runs.

Current decisions live in `CLAUDE.md` under Decisions. This log records how and why they were made.

---

## 2026-09-29 — Project setup and orientation

**What happened**
- Adopted Claude Code with the Everything Claude Code (ECC) plugin. The install uses the older plugin name, so commands carry the `/everything-claude-code:` prefix, and verification uses the `verification-loop` skill (there is no `quality-gate` command).
- Wrote a lean `CLAUDE.md` (invariants, scope, decisions, workflow) and moved detailed design into `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md` and `docs/GRAPH-LAYER.md`.
- The architect agent's orientation report found real gaps: missing docs, stack decisions referenced but not recorded, a truncated README, a wrong demo filename, and ticker and question-category mismatches between the demo and the runbook.

**Decisions**
- Stack: pgvector, Neo4j Community, Phoenix, Leiden in Python, uv.
- Kùzu rejected: its upstream was archived in October 2025.
- Chroma dropped: once Neo4j requires Docker, the "zero-server" advantage disappears, and Postgres can hold chunks, offsets and embeddings together.
- Tickers: NVDA, AMD, INTC (one sector, so disclosures are comparable).

**Lessons**
- Asking the agent to read and report before writing code caught contradictions that would otherwise have become code.
- An ECC hook blocked the agent from creating docs files unasked. Kept deliberately: agents shouldn't edit their own guardrails.

---

## 2026-09-30 — Step 1: repo and environment ✅

**Built**
- Repo structure per RUNBOOK, `pyproject.toml` with uv and a committed lockfile, `.env.example`, `.gitignore`.
- `docker-compose.yml`: Postgres 16 with pgvector 0.8.6 (`pgvector/pgvector:0.8.6-pg16`) and Neo4j 5.26.31 Community, credentials from environment variables, image tags pinned exactly.
- GitHub Actions CI: ruff and pytest on push, with a Postgres service container. `pytest-socket` blocks all network access except localhost.

**Evidence**
- Postgres `SELECT 1` succeeded; Neo4j HTTP returned 200; the `vector` extension was installed.
- First CI run green on GitHub.

**Issues caught in review**
- The first checkpoint report omitted the CI run, and Docker checks were unverified. Step 1 was not accepted until CI was green and both databases were checked.
- Image tags were floating (`neo4j:5-community`); they are now pinned.

---

## 2026-09-30 — Step 2a: EDGAR client, models, parser spike ✅

**Built**
- `ingest/models.py`: frozen Pydantic v2 models (`FilingMeta`, `ParsedSection`, `ParsedFiling`) with validators for offsets and safe filenames.
- `ingest/edgar_client.py`: User-Agent from the environment, at least 100 ms between requests (EDGAR allows 10 per second), on-disk cache in `data/raw/`, amended filings excluded and logged, fiscal period derived from EDGAR metadata.
- `ingest/parser_spike.py` (throwaway) and `spikes/parser_spike_report.txt`.

**Process (ECC)**
- `tdd-workflow`: tests written first and confirmed failing before implementation.
- `python-review`: 5 high and 7 medium findings, all fixed. They included path-traversal risk via `accession_no` and `primary_document`, unvalidated EDGAR response shapes, and a deprecated Pydantic API.
- `search-first`: current edgartools and sec-parser docs checked before writing the spike.
- `verification-loop`: final check before the checkpoint.

**Evidence**
- 51/51 tests passing, 100% coverage on `ingest/models.py` and `ingest/edgar_client.py`, ruff and pyright clean.

**Bugs found**
- **NVIDIA fiscal year.** NVIDIA's fiscal year ends in late January, on a date that shifts year to year. The first implementation labelled NVIDIA's FY2024 10-K as FY2025. The test caught it before any code shipped. The fix: for 10-Ks, use the report date's year.
- **API misuse in the spike.** In edgartools 5.x, `Document.text` is a method, not a property. The spike first read the function object instead of the text. It was caught during the docs check.

**Issues caught in review**
- The rate-limit test only asserted a 50 ms gap, which would have passed a client breaking EDGAR's limit. It was tightened to 100 ms.
- The first plan used hand-written HTML fixtures, which would have tested the parser against HTML invented by the same agent that wrote the parser. Replaced with excerpts from real filings.
- The first plan's `resolve()` test was circular: it compared a slice with itself. Replaced with a comparison against chunk text stored at chunking time.
- After the review fixes, the new validators were initially untested. Tests for them were added before committing.

**Parser spike results** (6 filings: one 10-K and one 10-Q per ticker)

| Metric | edgartools 5.59.1 | sec-parser 0.58.1 |
|---|---|---|
| Errors | 0 | 0 |
| Table-of-contents false positives | 0 | 0 |
| Tables detected | 640 | 325 |
| INTC filings | Parsed | Failed completely (cover page only) |
| 10-K support | Yes | No dedicated 10-K parser |
| Offset round-trips passing | 3 of 29 | Not applicable |

**Fiscal-period cross-check:** `derive_fiscal_period()` matched the XBRL `dei:DocumentFiscalYearFocus` and `dei:DocumentFiscalPeriodFocus` tags on all 6 filings, including NVIDIA.

**Decisions**
- Parser: edgartools. sec-parser rejected (no 10-K parser; complete failure on both INTC filings).
- Offsets: parsed text is built by concatenating the section texts edgartools returns, with offsets recorded at construction. Search-based offset recovery was rejected: duplicated text in filings makes it fragile.

**Open questions going into Step 2b**
- The INTC 10-Q is missing Part I Item 2 (MD&A).
- The NVDA and AMD 10-Qs are missing Part II Item 1A. Is the heading absent from the filing, or did the parser miss it?
- The INTC 10-K returned 10 sections, versus 24 for NVDA and AMD. Are the section boundaries correct?
- Do detected tables survive into section text in a form the chunker can recognise?

---

## 2026-10-01 — Step 2b: production parser ✅

**Built**
- `ingest/parser.py`: `parse_filing(html, meta) -> ParsedFiling` with a three-step fallback chain: (1) content anchor via edgartools `get_sec_section()`, (2) sequential heading scan in the document body, (3) cross-reference index lookup for required sections.
- `ingest/models.py`: added `ParsedTable` model, `tables` and `missing_sections` and `fallback_sections` fields to `ParsedFiling`.
- `tests/fixtures/`: 6 gzipped HTML fixtures (nvda_10k, nvda_10q, intc_10k, intc_10q, amd_10k, amd_10q) cut from real downloads in `data/raw/`.
- `tests/conftest.py`: session-scoped fixture pairs for all 6 filings.
- `tests/test_parser.py`: 58 tests (unit + 6-filing integration) in the RED-then-GREEN TDD cycle.

**Evidence**
- 116/116 tests passing; ruff clean.
- All 6 filings: required sections present (`missing_sections == []`), round-trip exact for every detected section, no overlap, no empty sections.
- INTC 10-K `part_ii_item_7` found via cross-reference index, not heading — starts at MD&A body content, not in the cross-reference table itself.
- NVDA/AMD 10-Q `part_ii_item_1a` found via heading fallback; tracked in `fallback_sections`.
- Tables embedded inline via `doc.to_markdown()`.

**Bugs found and fixed during implementation**
- **edgartools maps two keys to identical content (INTC 10-K).** `part_ii_item_8` and `part_iv_item_15` both anchor to the same line ("Financial Statements and Supplemental Details"). Without deduplication, the required `part_ii_item_8` got a zero-length slice. Fixed by deduplicating on position, keeping the canonically-earlier key.
- **Sequential heading scan over-advanced `next_min` (AMD 10-Q).** `part_i_item_2`'s content anchor placed it at 98.2% (cross-reference zone). In the heading scan, advancing `next_min` to 98.2% made `# ITEM 1A. RISK FACTORS` at 39.2% unreachable. Fixed by capping `next_min` advancement at the body boundary (90%).
- **Cross-reference index gave Part II items priority over Part I (INTC 10-Q).** The parser scanned the last 12% for `| Item N. | title |` rows; for 10-Q filings both Part I and Part II cross-reference tables are in that window and the dict overwrote Part I entries with Part II entries. Fixed by keeping the first occurrence per item number.
- **`part_iv_signatures` content anchor returned wrong position (NVDA 10-K).** The section text starts with executive officer content, so the first unique line appeared in the executive officers table at 15.5%. Signatures are always in the last 20% of a filing; the anchor search is now restricted to that range.
- **Signatures pattern false positive.** Bare `^signatures` (IGNORECASE) matched unrelated headings. Tightened to require a markdown heading `# Signatures` or a standalone bold `**Signatures**`.
- **`missing_sections` used `found` instead of `parsed_sections`.** A section that landed in `found` but was later skipped (empty slice) would not appear in either `sections` or `missing_sections`, silently giving a false "all present" signal. Fixed by computing `missing` from `parsed_labels` after construction.
- **`_find_content_anchor` counted occurrences from `body_min` but searched from `start`.** When `min_pos` was advanced past `body_min`, lines appearing before `start` were counted in the uniqueness filter but then not found by `md.find`. Fixed by aligning the count range with `start`.

**Issues caught in python-review**
- Two HIGH fixes applied: empty-md guard (`ValueError` if `to_markdown()` < 500 chars) and the `_find_content_anchor` count/start alignment.
- MEDIUM noted: `_build_section_positions` is 110 lines (guideline is 50). Left for Step 3 refactor when the function's role is finalised. `missing_sections` latent bug fixed.

**Open questions going into Step 3**
- How many table chunks exceed 1200 tokens? (Step 3 concern, deferred.)
- `part_iv_signatures` content anchor restriction (80%) is a constant; could be a filing-structure heuristic for other back-matter sections in future.
- Section positions for INTC 10-Q are partly in the ToC zone (2–3%). These are edgartools positions, not heading matches. The round-trip passes, but the extracted text includes ToC entries for those sections rather than body content. Logged for Step 3 review.

---

## Findings worth telling

Short versions of the stories from this build so far, for interviews and write-ups.

- **Test-first caught a real fiscal-calendar bug.** NVIDIA's fiscal year ends in late January, so a naive date calculation mislabels its filings by a year. A test written before the code caught it. The labels were then cross-checked against the filings' own XBRL tags.
- **Choosing a parser from evidence.** A one-day spike on real filings showed one popular library failing completely on Intel's filings. That would have silently removed a third of the corpus.
- **The citation guarantee nearly broke.** Only 3 of 29 section offsets from the library round-tripped. Instead of patching this with text search, offsets are now correct by construction.
- **Reviewing the agent's tests, not just its code.** Several agent-written tests would have passed while proving nothing: a loose rate-limit threshold, a circular round-trip test, and fixtures written by the same agent as the parser. Catching these is part of the job.