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

## 2026-10-01 — Step 2b: production parser ✅ (superseded — see the 2026-10-02 close-out)

> **Correction, 2026-10-02:** the "all 6 filings: required sections present" result below was a false pass. It tested that each required section existed, not that it was the right section. The full-corpus run found stubs and misplaced boundaries, and the position logic was rewritten. See the close-out entry.

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

## 2026-10-02 — Step 2b close-out: full-corpus run, boundary rewrite ✅ (decisions made; see the next entry for the final manifest and numbers)

**What was built**
- `ingest/corpus.py` (`python -m ingest.corpus`): picks the manifest, downloads (cached), parses, and writes `spikes/corpus_report.txt` with commit, edgartools version and thresholds in the header. Manifest rule, chosen by me because the repo had none: per company, the 2 newest 10-Ks and the 6 newest 10-Qs (24 filings).
- `parse_filing_with_methods()` returns the extraction method per section; `parse_filing()` is unchanged.
- `scripts/build_fixtures.py` rebuilds `tests/fixtures/*.html.gz` from `data/raw/` (byte-identical to the originals).
- `tests/test_parser_substance.py` (substance checks on the 6 fixtures) and `tests/test_corpus_local.py` (all cached filings; skipped in CI because `data/` is gitignored).
- edgartools constraint tightened from `>=2.0` to `>=5.59,<6.0` in both dependency groups; lock re-resolved (the installed version, 5.59.1, did not change).
- `docs/BUILD_LOG.md` merged into `docs/BUILD-LOG.md`. The hyphen file did not exist, so this was a `git mv`.

**Evidence** (commit 16bd753, edgartools 5.59.1; `spikes/corpus_report.txt`)
- 24 of 24 filings have all three required sections. Required-section extraction methods over 72 sections: **edgartools 21, heading 33, cross_reference_index 18.**
- Under 2,000 characters among required sections: 2, both NVIDIA 10-K Item 8 (211 chars). It is a pointer to Item 15 ("set forth in our Consolidated Financial Statements"); the statements are in `part_iv_item_15` (over 100k chars). See Decisions.
- Tables, summed over 24 filings: 2,290 detected; 1,812 inserted into section text; 82 not inserted; 396 indeterminate (purely numeric tables, no label to match). "Inserted" is an upper bound: a table counts when 80% of its row labels appear anywhere in the section text. Per filing, not-inserted was 1–13, mostly cover-page and pre-first-section tables.
- INTC 10-K Item 7 (FY2025, 61,051 chars) starts `| Management's Discussion and Analysis |` / `Overview` and ends `... see "Note 6: Other Financial Statement Details" ... | MD&A | 32 |` (a page footer).
- Tests: 348 passed, 1 skipped (a documented-pointer case). Coverage 98% on production modules; overall 67% only because the throwaway `ingest/parser_spike.py` is at 0%. ruff and pyright clean on `ingest/` and `scripts/`.
- CI: green on 16bd753. The preceding push (e39c3c3) was red because I committed a line-length lint error; fixed in the next commit.
- Anchor-tolerance sensitivity (24 filings): 400 and 800 characters give identical results; 200 moves two required sections from edgartools to heading.

**Bugs found** (all by the full-corpus run; none visible on the 6 tuned filings)
- **False pass in the first Step 2b result.** `missing_sections == []` only meant a section existed. INTC 10-Q Item 2 and Item 1A were 69–246-character slices of the cross-reference/ToC rows in all 6 INTC 10-Qs; AMD 10-Q Item 1 (financial statements) was the exhibit list at 98.9%, 2–3k chars; INTC 10-K sections were cut by edgartools anchors that landed mid-sentence, on an index row, or in the wrong item.
- **Root causes.** edgartools content anchors are noisy and were trusted first; the 2% body floor hid AMD's 1.7% Item 1; the cross-reference title search matched ToC rows; title matching took a repeated sub-heading ("# Risk Factors") ahead of the section's own title row; an anchor inside a table dropped the top of the income statement (INTC 10-Q lost "Net revenue"); anchors placed after `# ` left a stray `#` at the end of the previous section.
- **Fix.** Headings and cross-reference body titles are now the reference. An edgartools anchor is used only if it agrees with the reference (within 400 characters) or, with no reference, starts a line and is not a ToC row. Anchors are snapped to table starts and line starts. Title matching is tiered (one-cell row, then `#` heading, then bare line). A statement-caption fallback covers a financial-statements section with no heading or title. Non-canonical edgartools keys are ignored.
- **Method labels.** The first rewrite kept the old convention of labelling a heading-positioned section "edgartools" if edgartools knew the key. python-review (HIGH) said this overstated edgartools. Labels now say where the position came from; `fallback_sections` keeps its old meaning (sections edgartools did not locate itself) so the existing NVDA 10-K "no fallback" test is unchanged.

**Review catches**
- python-review: 1 HIGH (label honesty, fixed), MEDIUMs fixed: 90-line function split, prose false-positive in the statement-caption pattern (stricter, tested), heading-floor cascade guard (tested), per-filing error handling in the CLI (tested), tautological tests replaced, first-line title check instead of first-300-chars, helper unit tests added. Not done: `classify_tables` window check (limitation stated in the report header instead).
- /code-review (Standards + Spec sub-agents, fixed point 1037c9f): fixed the over-long function and magic numbers; written up the missing BUILD-LOG entry (this one) and the push. One reported finding was wrong (the edgartools pin is not duplicated: line 11 is the main group, line 21 the spike group). Not available to the Spec review: the earlier "revised-plan instructions".
- The new tests for the substance checks and the corpus module were written first and shown failing (the substance tests failed 15 ways against a stub). Two fixes came from the corpus run first and were then covered by tests afterwards: the statement-caption fallback (INTC 10-Q Q1-25 had no Item 1) and the dangling `#`. One of my own test expectations was wrong and was corrected after checking the filing (INTC FY2025 10-K's body heading is `| Risk Factors |`, not `| Risk Factors and Other Key Information |`); the assertion still requires the section to start at that heading.

**Decisions**
- Manifest: 2 newest 10-Ks + 6 newest 10-Qs per company. Includes all six fixture filings.
- Section position: heading / cross-reference title first, edgartools anchor only if it agrees. This reverses the Step 2b order (edgartools first).
- **Pending, need your call:** (1) NVIDIA Item 8 stub is kept as a documented, tested exception, not a bug fix. Recommendation: keep flagged; in Step 3 treat `part_iv_item_15` as the financial-statements content for such filings. (2) Fixtures are full filings, not excerpts, against CLAUDE.md. Recommendation: amend the CLAUDE.md wording; parser tests depend on whole-document positions.

**Limitations (known, not fixed)**
- Content before the first located section (cover page, forward-looking statements, glossary) is in no section and is dropped from `ParsedFiling.text`; up to about 8% of an INTC 10-Q.
- INTC lays its filings out in a non-canonical order. Required sections are verified (substance tests plus first/last characters of all 72), but non-required INTC 10-K sections located only by edgartools anchors (Items 1C, 2, 5, 7A, 10 and so on) are unverified, and INTC 10-Q Item 1A spans from the Risk Factors heading to the exhibit list.
- Only first and last characters were checked across the corpus. The RUNBOOK's "spot-check three by eye" has not been done by a person.
- `/everything-claude-code:save-session` is not in this install's skill list, so it was not run.

**Open questions**
- Does Step 3 want an explicit `part_iv_item_15`-as-financial-statements mapping, or a table-schema field that records where a filing's statements live?
- `docs/OPEN-DECISIONS.md` has an uncommitted edit in the working tree (an RTX line removed) that I did not make.

**Next session starts with**
1. Your answers on the two pending decisions (NVIDIA Item 8 exception, fixtures wording) and whether to commit `.claude/skills/` and `skills-lock.json` (Matt Pocock skills installed at project scope).
2. The "Before Step 3 — setup session" items in `docs/OPEN-DECISIONS.md` (ECC upgrade, model switch, context-budget check).
3. Then plan Step 3a (table schema) with `/everything-claude-code:plan` and wait for approval. Step 3 has not been started.

---

## 2026-10-02 — Step 2b close-out, round 2: decisions applied ✅

**Decisions received and applied**
1. NVIDIA 10-K Item 8 (211 chars) stays as a verified, tested exception; Step 3 treats `part_iv_item_15` as the financial statements when Item 8 is a pointer. Recorded in CLAUDE.md Decisions and OPEN-DECISIONS (Step 3).
2. CLAUDE.md fixtures rule amended to "real downloaded filings (gzipped)".
3. `.claude/skills/` and `skills-lock.json` committed separately (f54255c). `.claude/settings.local.json` left untracked. Note: the installed `grill-me` skill only says "Call the Skill tool with 'grilling'" and the `grilling` skill is not installed, so `grill-me` does nothing yet.
4. **Manifest changed** to the two most recent complete fiscal years per ticker: each 10-K plus its three 10-Qs, aligned by `fiscal_period`. Rule recorded in CLAUDE.md Decisions. Six filings outside this rule (the in-progress year: NVDA FY2027 Q1/Q2, AMD FY2026 Q1/Q2, INTC FY2026 Q1/Q2) stay cached and in the fixtures; the local test still covers them.
5. **INTC 10-K Item 7 end, verified against the filing.** The page-32 footer was where Item 7's main block ends (Intel's index: "Liquidity and capital resources, Pages 29-32"), so that end was right. But the same index lists "Critical accounting estimates, Pages 34-36" under Item 7, and Intel prints it after Item 7A, so it sat inside the Item 7A section. MD&A was truncated. Fix, test-first: a required item can now own several spans, located from sub-rows of the filer's cross-reference index. FY2025 Item 7 is now 2 spans (77,643 chars); the test pins the real last sentence of MD&A ("...our results of operations and financial condition could be materially adversely affected.", then the `| MD&A | 36 |` footer) and that Item 7A no longer contains Critical Accounting Estimates. FY2024 already had it in Item 7.
6. **Preamble.** Text before the first located section is now a `preamble` section, so offsets cover the whole filing. Tested for all six fixtures and all 30 cached filings: whitespace-stripped section text equals the whole markdown. Open for Step 3: the preamble includes the ToC and the Intel cross-reference text (30k chars in INTC FY2024 10-K), so the chunker must decide whether to embed it.
7. `spikes/investigate_2b.py` deleted (my scratch file).
8. `save-session` removed from the CLAUDE.md Workflow.
9. Step 2b acceptance checks written into docs/RUNBOOK.md (amended section) so `/code-review` checks them from the repo.
10. OPEN-DECISIONS: Step 3 now says place tables by document position, target zero indeterminate tables, report detected vs placed; and use `part_iv_item_15` as financial statements when Item 8 is a pointer. The resolved "Step 2b — close out" items were removed from that file, per its own rule.

**Evidence** (commit e492e5e, edgartools 5.59.1; `spikes/corpus_report.txt`)
- 24 of 24 filings: all three required sections present. Methods over 72 required sections: **edgartools 20, heading 34, cross_reference_index 18.** No missing-quarter warnings: all 24 manifest filings were found.
- Required sections under 2,000 chars: 2, both NVIDIA 10-K Item 8 (211 chars), the documented exception.
- Tables (24 filings): 2,338 detected; 1,876 inserted; 24 not inserted; 438 indeterminate. Not-inserted fell from 82 to 24 because the preamble now keeps the cover-page and ToC tables. The 438 indeterminate (numeric-only) tables are what the Step 3 table-placement item targets.
- Tests: 451 passed, 1 skipped (the documented pointer case); coverage 97-98% on `ingest/corpus.py` and `ingest/parser.py`; ruff (whole repo) and pyright (production modules) clean.

**Review catches (python-review of the preamble/span/manifest change)**
- HIGH: the continuation search could run to the end of the body when no later required section was located, matching generic titles inside unrelated sections. Fixed: no search without a later required section; generic titles ("None", "Not applicable") and titles under 10 characters ignored.
- HIGH: index sub-topics are keyed by item number only, so 10-Q Part I and Part II items collide. Fixed: items whose number appears in both Parts are skipped.
- HIGH: a continuation span was labelled `cross_reference_index`, which would mark an edgartools-located item as a fallback. Fixed with a distinct `continuation` method that is never a fallback.
- MEDIUM, fixed: `PREAMBLE_KEY` constant, `select_manifest` split, tests join all spans of a label, the local corpus test parses each file once, tests added for empty/preamble-only/zero-offset/duplicate-period cases. Not fixed: nothing checks canonical order on INTC 10-K non-required sections (see Limitations).

**Limitations / open**
- INTC FY2024 10-K non-required sections located only by edgartools anchors are mislabeled or out of canonical order (Items 5, 2, 1C, 10, 3). Required sections are verified. Known since the first close-out; no test catches it, and I did not add one that would fail or be marked xfail.
- Whole-filing coverage is verified on whitespace-stripped text, not byte for byte.
- The RUNBOOK's "spot-check three by eye" is still a person's job.

**Next session starts with**
1. Setup session items in `docs/OPEN-DECISIONS.md` ("Before Step 3"): ECC upgrade, model switch, context-budget check. The Matt Pocock skills line there is done (f54255c) except `grilling`, which `grill-me` needs.
2. Plan Step 3a with `/everything-claude-code:plan` and wait for approval: table schema, tables placed by document position, `preamble` handling, `part_iv_item_15` as statements when Item 8 is a pointer. Step 3 has not been started.

---

## 2026-10-03 — Step 3a: table spans ✅

Model: Claude Opus 5.5 (claude-opus-5-5) for planning, implementation and reviews. Fixed point e88cc45.

**Setup and decisions (commit e88cc45)**
- The Step 3 plan is in `.claude/plans/step-3.md`. The user accepted all six recommendations, which are now in CLAUDE.md Decisions: three sub-steps; tables located by position in the text; tiktoken cl100k_base; the preamble is chunked; deterministic chunk IDs with chunker_version; and the `filings` and `chunks` tables.
- The Matt Pocock `code-review` skill was replaced by a project skill, `step-review`, which reads its spec from the plan file, the RUNBOOK and OPEN-DECISIONS rather than an issue tracker. CLAUDE.md now says to apply the unslop skill to all prose.
- Deferred to after Step 3: the ECC upgrade (it renames the workflow commands) and its context-budget command, which ECC 1.4.1 lacks. As a stand-in, I measured the always-loaded instruction files at about 4.3k tokens.
- An ECC hook blocks new `.md` files outside a short allowlist. The user approved one bypass, for the skill file. Plans live in `.claude/plans/`, which the hook allows.

**What was built**
- `ingest/tables.py`:
  - `find_table_spans(filing)` returns a frozen `TableSpan` for each table: exact offsets, its section label and index, the row count, unclosed rows and a glued-header flag.
  - `tables_crossing_sections(filing)` scans the whole text for tables that a section boundary cuts.
  - `reconcile_tables(filing)` places each table edgartools renders in the text and counts the outcomes.
- **Row rules.**
  - A row starts at a line beginning with `|` and ends at the first line ending with `|`, because cells can hold line breaks (Intel's "Exhibit\n\n\nNumber").
  - A row that reaches another row, the end of the section or 20 lines before it closes counts as unclosed and ends at its own line.
  - A blank line ends a table.
- `ParsedTable.markdown` now holds edgartools' own rendering of each table, which is the exact text in the filing, using its per-table renderer `MarkdownRenderer._render_table`. That method is private, and no public equivalent exists in 5.59. Tables that render empty are kept, so the list matches edgartools' count.
- Removed:
  - `ParsedTable.section_key`, by decision 4;
  - the hand-written `_table_to_markdown` and its two unit tests;
  - the Step 2b label-matching table accounting (`classify_tables`, "inserted"/"indeterminate") and its four tests.
- The corpus report now prints the reconciliation per filing, with the identity in the report header.
- `tests/table_checks.py` holds the acceptance checks shared by the fixture tests and the local corpus tests.

**Evidence** (commit 9437e6a; edgartools 5.59.1)
- Tests: 634 passed, 1 skipped, the documented NVDA pointer case.
- Coverage: `ingest/tables.py` 98%, `corpus.py` 97%, `parser.py` 99%.
- ruff is clean. pyright (run through uvx) reports 0 errors on the production modules.
- Over the 30 cached filings in `data/raw`, the 24-filing manifest plus 6 from the year in progress:

  | Measure | Count |
  |---|---|
  | edgartools tables | 2,793 |
  | Table spans | 2,717 |
  | Rendered empty (no content columns) | 59 |
  | Sharing a span with the table before (no blank line between) | 24 |
  | Spans with no edgartools table starting in them (INTC one-cell title rows such as `\| Consolidated Condensed Statements of Operations \|`) | 7 |
  | Renderings not found in the text | 0 |
  | Renderings starting outside every span | 0 |
  | Tables crossing a section boundary | 0 |
  | Unclosed rows | 6 |
  | Glued headers | 6 |

  The counts reconcile exactly: 2,793 − 59 − 24 + 7 = 2,717. Tests check the identity, and the two zeros, on every fixture and cached filing.
- The user ran `python -m ingest.corpus` over the 24-filing manifest, producing `spikes/corpus_report.txt`.
  - The first run was on the uncommitted tree (header e88cc45+dirty). A rerun on the clean commit 9437e6a gives an identical report apart from the header line, which now reads commit 9437e6a.
  - Every filing has 0 renderings not in the text, 0 starting outside a span and 0 tables crossing a section.
  - Totals: 2,338 edgartools tables, 47 rendered empty, 15 sharing a span and 5 spans without a table, giving 2,281 table spans (2,338 − 47 − 15 + 5).
  - There are 6 unclosed rows, in the NVDA, AMD FY2024 and INTC 10-Ks, and 4 glued headers, in INTC 10-Qs.
  - All 24 filings have their required sections. The method counts are edgartools 20, heading 34 and cross_reference_index 18, unchanged from Step 2b, so the parser change moved no section.

**Bugs found (both are edgartools rendering glitches; tests now pin each one)**
- **An unclosed row merged two tables.**
  - In the NVDA FY2026 10-K, an income-tax table row runs into a prose sentence on the same line.
  - The first row rule kept reading until a later line ended with `|`. That was the next table's header, so two tables and the sentence between them became one span.
  - A pinned test ("NVDA has an unclosed row") found it. The regression test was written together with the fix, not before it.
- **Header rows were glued to prose.**
  - In 6 INTC 10-Qs, edgartools prints a table's header row at the end of the paragraph before the table, so those spans started at the `| --- |` separator row.
  - The reconciliation found it: 6 renderings started outside every span. Tests came first, and they failed on exactly those 6 filings and the fixture.
  - Fix: a span that starts with a separator row starts instead at the header row. That row begins at the n-th `|` from the end of the previous line, where n is the separator's pipe count.
- One of my own test expectations was wrong: the INTC 10-Q exhibit table starts one row above the multi-line header, at `| | | Incorporated by Reference |`. I checked it against the filing and corrected the test.

**Review catches**
- **python-review.** No CRITICAL or HIGH findings. MEDIUM fixes:
  - an earlier `|` in the prose could pull a glued-header span too far back, now fixed by counting cells, test first;
  - `SEPARATOR_ROW` was copied into the tests, and is now imported;
  - a magic `rows=2`;
  - the row scan could peek past the section end.
  - Two findings were not fixed: `model_config` stays a dict to match the repo, and CRLF input is noted as unsupported, because edgartools writes LF only.
- **step-review, Standards.**
  - Fixed: field names (`edgartools_tables`, `span_count`, `crossing_tables`), `ge=0` on span counts, and the duplicated test bodies, now in `tests/table_checks.py` and `span_containing`.
  - Not fixed, both judgement calls: `count_tables` stays in `corpus.py`, because it is report formatting. Bare `(start, end)` tuples remain in `tables_crossing_sections`.
- **step-review, Spec.**
  - Fixed: the plan asks for the gap to be explained, and the report only showed counts. The reconciliation now explains it per filing.
  - Fixed: removing the "inserted" accounting left nothing checking that edgartools' tables reach the text. The reconciliation checks that, using edgartools' own renderings, which is evidence independent of the span scanner.
  - Open: the regenerated 24-filing report.
  - Noted for 3b: an unclosed row's span includes the prose glued onto its line (the NVDA sentence ending "...as follows:"). The chunker will treat that prose as table text.

**Limitations**
- The 7 spans with no edgartools table are explained by type (one-cell title rows), not table by table. The ordered search can match a repeated title to an earlier copy of it.
- Unclosed and glued-header spans contain some prose (6 + 6 cases over 30 filings).
- The reconciliation depends on a private edgartools method. The tests fail loudly if it changes.

**Next session starts with**
1. Plan Step 3b (the chunker) as an addition to `.claude/plans/step-3.md`, and wait for approval. Its fixed point is the 3a commit.
2. In that plan, decide how a chunk treats the prose inside unclosed or glued-header spans (12 spans over 30 filings), and whether to accept the private edgartools renderer that the reconciliation relies on.

---

## 2026-10-03 — Step 3b: chunker ✅

Model: Claude Opus 5.5. Fixed point de340b7. I ran the whole workflow while the user was away, after a planning session in which they answered four questions.

**Decisions taken with the user before they left** (recorded in `.claude/plans/step-3.md`, "3b detailed plan")
- 3b-1: prose that edgartools glued onto a table row stays in the table block.
- 3b-2: overlap is text only. It never reaches back past a table, so a chunk after a table starts at the table's end. A table chunk may take up to 100 tokens of the text before it, usually its caption.
- 3b-3: each chunk has `contains_table`.
- 3b-4: commit locally, don't push.

**What was built**
- `ingest/chunker.py`:
  - `chunk_filing(filing)` returns frozen `Chunk` records holding the RUNBOOK fields plus `ordinal`, `token_count`, `tokenizer` (cl100k_base), `chunker_version` ("1") and `contains_table`.
  - Chunk IDs are `{accession_no}:{ordinal:04d}`.
- **Units.** Each section span is cut into units: whole table spans, and blank-line paragraphs between them.
  - A paragraph over 800 tokens splits at sentence ends. The sentence pattern handles edgartools' escaped `\.`.
  - A sentence over 800 tokens splits into runs of whole words, sized by exact token count.
  - A single word over 800 tokens is cut by characters.
- **Packing.** Units pack greedily while the chunk's exact token count stays at 800 or under.
  - A new chunk starts up to 100 tokens back, at a sentence or unit start.
  - A text chunk's overlap shrinks to keep the chunk within the limit.
  - The earliest valid overlap start is found by binary search, because both limits loosen as the start moves later.
- The corpus report gains per-filing chunk counts: total, with tables, over 800 tokens and maximum tokens.
- `tests/chunk_checks.py` holds the acceptance checks, shared by the fixture and local corpus tests. It recounts tokens with tiktoken directly.
- The checks:
  - offsets and token counts round-trip;
  - IDs are unique, in order and strictly increasing;
  - each chunk lies in exactly one section span;
  - no table is split, and `contains_table` is exact;
  - chunks start at a section start or after whitespace;
  - only table chunks exceed 800 tokens;
  - sections are covered;
  - overlap is 100 tokens or less and never includes a table.

**Evidence** (working tree on de340b7, committed as the 3b commit)
- Tests: 695 passed, 1 skipped. Coverage: `chunker.py` 99%. ruff is clean, and pyright (via uvx) reports 0 errors.
- The 30 cached filings (the 24-filing manifest plus 6) produce:

  | Measure | Value |
  |---|---|
  | Chunks | 2,566 |
  | Chunks with a table | 1,415 |
  | Chunks over 800 tokens | 111, all table chunks |
  | Largest chunk | 1,825 tokens, an INTC 10-Q statement table |
  | Median chunk | 723 tokens (p10 354, p90 796) |
  | Chunks under 50 tokens | 64, mostly one-line items such as "Item 1B. None" |

- Overlap: 1,957 of 2,249 transitions within a section overlap, with a median of 78 tokens and a maximum of 100. The other 292 mostly follow a table.
- All chunk checks pass on every cached filing. Parse plus chunk takes about 21s for 30 filings.
- The user ran `python -m ingest.corpus` on the clean commit abbb145, adding a chunk table to `spikes/corpus_report.txt`. The 24 manifest filings produce:
  - 2,144 chunks, 1,176 of them with a table;
  - 93 chunks over 800 tokens, all table chunks;
  - a largest chunk of 1,801 tokens.
  - Every other section of the report is unchanged from 3a.

**Review catches**
- **python-review, HIGH, fixed.** Word runs were sized by an additive token estimate that assumed single spaces. Tab-separated words reached 1,040 tokens, and one 20k-character word reached 2,500 tokens, and nothing caught it. The tests came first: tab-separated words, and one ~4,000-token word. Runs are now sized by exact count, with a binary search.
- **python-review, MEDIUM, fixed.**
  - Overlap could not start inside the previous chunk's last unit.
  - Trying every overlap start was quadratic (11.7s on a 2 MB synthetic section); it now uses binary search.
  - There were no tests for `\.)` or "U.S.".
- **python-review, LOW, fixed.**
  - Four weak tests were tightened: sentence-start overlap, glued prose, strictly increasing starts, and `contains_table` false when there is no table.
  - A whitespace-start check was added.
- **step-review, Standards.**
  - Fixed: names (`section_tables`, `ChunkCounts.total`) and the check's error message.
  - Not changed, all judgement calls:
    - the two small binary searches;
    - `_Unit` and `_Span` having the same shape;
    - the chunk_id format kept as a string until 3c's `resolve()` needs to parse it;
    - `corpus.py` running both the parser and the chunker.
- **step-review, Spec.**
  - Fixed: a determinism test on a real fixture.
  - Accepted: scope beyond the plan (cutting a word over the limit by characters, the stricter whitespace-start check). Abbreviations over-split sentence starts, so an overlap can begin mid-sentence (after "U.S. "); this is documented in `sentence_starts`.
  - Two findings need the user; see Decisions needed.

**Decisions** (raised by the reviews, settled by the user on their return, both as recommended)
1. **CI made a network call.**
   - tiktoken downloads `cl100k_base` (1.68 MB) when `ingest.chunker` is imported. pytest-socket blocks the network only while tests run, not while modules are imported, so CI would have called openaipublic.blob.core.windows.net on every fresh runner. I reproduced this with an empty cache.
   - Fix, test first: the encoding file is vendored as `vendor/tiktoken/9b5ad71b...`, named by tiktoken's cache key. Its SHA-256 matches tiktoken's expected hash (223921b7...). `tests/conftest.py` sets `TIKTOKEN_CACHE_DIR` to it.
   - `tests/test_tokenizer_offline.py` checks the hash, and that the chunker tokenizes in a subprocess whose sockets raise on use. All 3 tests failed before the fix.
   - A python-review of the fix found no blockers. Applied from it:
     - The CI workflow also sets `TIKTOKEN_CACHE_DIR`, for any future step that imports the chunker outside pytest.
     - The offline test blocks DNS lookups too.
     - `.gitattributes` marks `vendor/**` as binary, so line-ending conversion can't break the hash.
     - `vendor/tiktoken/README.md` records the source URL and hash.
   - tiktoken is pinned at 0.14.0 in uv.lock. An upgrade that changed its cache-key scheme would make the offline test fail.
   - Tests: 698 passed, 1 skipped.
2. **Coverage wording.** The user accepted "every non-whitespace character of every section is in at least one chunk". It is recorded in the plan and in CLAUDE.md Decisions.

**Next session starts with**
1. Plan Step 3c: the Postgres `filings` and `chunks` tables, `db/schema.sql`, `resolve()`, the loader that writes `data/parsed/`, and the database-reviewer agent.

---

## 2026-10-03 — Step 3c: Postgres and resolve() ✅ (Step 3 stop condition met)

Model: Claude Opus 5.5. Fixed point fdc87fc. Planned with the user, who answered four questions, approved the schema explicitly, and added three CHECK constraints after the database review.

**Decisions** (in `.claude/plans/step-3.md`, "3c detailed plan")
- 3c-1, two steps:
  - `python -m ingest.corpus` (network) also writes `data/parsed/{accession_no}.json`.
  - `python -m ingest.load` (offline) chunks and loads Postgres from those files.
- 3c-2, a throwaway schema per test session. Tests skip without `DATABASE_URL`, and a guard test fails in CI if it's unset.
- 3c-3: no sections table yet.
- 3c-4: `--verify` command plus fixture tests.
- Schema approved. Added after the database review: CHECKs on the chunk_id format, `chunks.form_type`, and `text_sha256` equal to the SHA-256 of `parsed_text`. `STORAGE EXTERNAL` was declined.

**What was built**
- `db/schema.sql`: the `filings` and `chunks` tables, with constraints and indexes.
- `ingest/store.py`:
  - `apply_schema` creates the tables, then fails with `SchemaMismatch` if the live columns differ from the code or the server isn't UTF-8.
  - `load_filing` checks the chunks, upserts the filing, replaces its chunks in one transaction, and commits.
  - `resolve(chunk_id)` runs `substr(parsed_text, char_start + 1, char_end - char_start)`. `get_chunk`, `stored_offsets` and `financial_statements_section` complete the module; the last gives `part_iv_item_15` for NVIDIA's pointer Item 8.
- `ingest/parsed_files.py`:
  - `ParsedRecord` (meta, filing, parser_commit) is checked for a matching accession.
  - Writes are atomic, and file names are checked against the accession inside.
- `ingest/load.py`, the loader and verifier. Exit codes: 0 ok, 1 verify failed, 2 bad input or no database, 3 load error. A connection error prints only its type, and a load error is cut to 500 characters.
- `ingest/provenance.py`: `git_state()`, moved from `corpus.py`. Untracked files under the code paths now mark a run `+dirty`.
- Shared helpers:
  - `make_chunk_id` in `chunker.py`;
  - `text_sha256` and `DEFAULT_PARSED_DIR` in `parsed_files.py`;
  - `MIN_SECTION_CHARS`, now in `parser.py` and re-exported by `corpus.py`.

**Evidence**
- `python -m ingest.load --verify` on the 24 manifest filings, in the development database, passed. The report is `spikes/load_report.txt`, rerun on the 3c commit.

  | Check | Result |
  |---|---|
  | Filings loaded | 24 |
  | Chunks | 2,144 (matches the corpus report) |
  | Seeded random sample (seed 20261003) | 20 of 20 resolved exactly; this is the RUNBOOK stop condition |
  | All chunks through `resolve()` | 2,144, 0 mismatches |
  | Stored offsets and text hashes vs the files | 0 differences |
  | Run time | about 11s |

- Tests: 745 passed, 1 skipped, with `DATABASE_URL` set. Without it, the 15 database tests skip and the CI guard passes.
- The fixture tests resolve every chunk of the 6 fixtures, plus a seeded sample of 20, in CI's Postgres.
- Coverage: `store.py` 98%, `load.py` 94%, `parsed_files.py` 100%.
- ruff is clean, and pyright reports 0 errors with no `type: ignore` left.
- `data/parsed` was written by the user's corpus run on fdc87fc+dirty. `git diff fdc87fc` shows no change to `parser.py`, `models.py`, `tables.py` or `chunker.py`, so the parse is the fdc87fc parse.

**Review catches**
- **python-review, HIGH:** `--verify` passed vacuously, reporting PASS with 0 of 0 on an empty parsed directory or a filing with no chunks. Tests came first. It now fails on no input, no chunks, no sample, a hash mismatch, or an ID or offset mismatch.
- **python-review, MEDIUM:**
  - A malformed `DATABASE_URL` could be echoed in a connection-error traceback. Now only the error type is printed, and a test covers it.
  - Any error was reported as "could not connect".
  - Nothing tested atomicity. A duplicate-ID load now raises, and the old rows stay intact.
  - Parsed files weren't written atomically.
- **database-reviewer, HIGH:**
  - `CREATE TABLE IF NOT EXISTS` would hide schema drift; fixed with the live-column check.
  - Offsets past the end of the text would make `substr` truncate silently; `load_filing` now refuses them.
- **database-reviewer, MEDIUM:**
  - The chunks' cik, form type and period could disagree with their filing; refused in code and partly in the schema.
  - The test search path left out `public`, where Step 5's vector type will live.
  - A docstring claimed bulk resolution saved decompression; it saves round trips.
- **step-review, Spec:**
  - The approved `chunks(accession_no)` index was missing, and is restored.
  - "Every chunk" was checked through a bulk query, not `resolve()`; every chunk now goes through `resolve()`, sliced at the stored offsets.
  - The end-before-start test used an invalid chunk ID, so it would have passed even without its CHECK. It's now a parametrized test with a passing control row.
  - The `form_type` and `char_start` CHECKs are now tested.
- **step-review, Standards:** duplicated constants and helpers consolidated (listed above).
  - Not changed: local list accumulators (never shared), `run()`'s two flags, and the denormalised (cik, form type, period) trio. That trio is deliberate, for filtering.

**Decisions** (the user accepted all three recommendations on 2026-10-03)
1. **The chunk_id CHECK wording.** It uses `lpad(ordinal::text, greatest(4, length(ordinal::text)), '0')`, not the approved `lpad(ordinal, 4, '0')`, which truncates ordinals of 10,000 and above. Kept.
2. **CHECKs beyond the approved list:** the accession_no pattern, `filings.form_type IN ('10-K','10-Q')`, `ordinal >= 0` and `token_count >= 0`. Kept.
3. **Provenance change.** Untracked files under ingest/, db/, tests/ or scripts/ now mark a run `+dirty`, where before only tracked changes did. Kept; it's stricter for invariant 2.

**Next session starts with**
1. Check that CI passed on the 3c push (`gh run list`). CI's Postgres runs the database tests, and the guard makes sure they don't skip.
2. Step 3 is complete. Next is Step 4: write the eval set by hand. Read the "Step 4" items in `docs/OPEN-DECISIONS.md` first (FinRank, hard negatives, invariant 1 wording, RESEARCH-PLAN). Per the workflow, the research design needs a grilling session with the user, and the `grilling` skill that `grill-me` calls is still not installed.

---

## 2026-10-04 — Step 4: eval tooling and an agent-drafted candidate set ✅ (the stop condition needs the user)

Model: Claude Opus 5.5, for the main session and the drafting subagents. Fixed point ed8ec01. The user asked for Step 4 to run unattended, including "a subagent set to create my own set". Plan: `.claude/plans/step-4.md`.

**Invariant 1 and the RUNBOOK**
- The RUNBOOK says the eval set is "written by Rohan, not by a coding agent", and invariant 1 forbade agent-written questions. The user's explicit instruction was followed in the form OPEN-DECISIONS had proposed:
  - agent drafts go only in `eval/agent_drafted_set.jsonl`, labelled `agent_drafted`, and are reported separately;
  - a draft counts as human only after the user accepts it with `python -m eval.review`.
- Invariant 1 is reworded to match. The RUNBOOK has an amendment note. The stop condition is unchanged: 120 human-written or human-verified records.

**FinRank** (searched 2026-10-04; github.com/datanxt/FinRank and arXiv 2608.07400)
- 1,185 human-written records over 22 companies in pharmaceuticals, oil & gas and automotive. No NVDA, AMD or INTC, so it can't be our question set. License CC BY-NC 4.0.
- Its schema ideas are borrowed:
  - topic, difficulty, reasoning and evidence-scope labels;
  - hard negatives labelled by relation;
  - a hash of each gold passage's text.
- An external check against its 5,230-passage corpus is proposed for Steps 5–6.

**What was built** (`eval/`, test-first)
- `schema.py`: the frozen `EvalRecord` (extra fields forbidden).
  - Classes: lookup, local, multi_hop, global, decline and unanswerable.
  - Gold chunks are pinned by SHA-256. Each answerable question has 1–2 hard negatives labelled `same_company_other_period`, `same_company_same_filing` or `peer_company`.
  - Provenance is `human_written`, `human_verified` or `agent_drafted`; a `human_verified` record needs `derived_from`.
  - `draft_key` (a hash of the question and gold chunks) and a 30% `test` split by question hash. Writes are atomic.
- `corpus_index.py`: every chunk, from `data/parsed` through the same chunker. Offsets are cached, keyed by the parsed files' hashes and the chunker version. Searches all words as prefixes.
- `validate.py`: checks that
  - the provenance fits the file;
  - the author fits the provenance;
  - the class fits the evidence scope;
  - the split matches the question hash;
  - gold chunks resolve, with unchanged text;
  - ticker and period labels equal the gold chunks';
  - hard-negative relations hold, with at least one per answerable question;
  - IDs and questions are not repeated.

  `--kind` overrides the file-name rule. `--db` re-checks through `resolve()` in Postgres. In the human set, each `human_verified` record needs an accept in the review log.
- `coverage.py`, `search.py` (with `--show`), and `merge_drafts.py`. The merge drops repeated questions, renumbers, recomputes splits, and writes only a valid set.
- `review.py` with `review_log.py`. The user accepts or rejects each draft. Decisions are keyed by `draft_key`, so they survive a re-merge. The tool refuses non-interactive input, so a script can't mint `human_verified` records. CLAUDE.md now forbids agents from running it or writing the human set or the log.
- Drafting: 8 subagents worked from `.claude/plans/drafting-brief.md`, one slice each.
  - Six stopped on the first run: one stalled, and five hit the session rate limit. They were relaunched in batches of three and told to write their files as they went.
  - The slices sit in `eval/drafts/` (gitignored). The merged file records each slice in its `author` field.

**Evidence** (working tree on ed8ec01, committed as the Step 4 commit)
- `eval/agent_drafted_set.jsonl` holds 140 records, all `agent_drafted`. Class counts match the targets exactly: lookup 40, local 35, multi_hop 30, global 15, decline 10, unanswerable 10.
  - Tickers: NVDA 68, AMD 64, INTC 59 (a record counts once per ticker it covers).
  - Split: dev 93, test 47.
  - Difficulty: easy 19, medium 83, hard 38.
- `eval.validate` finds 0 problems offline and 0 with `--db`. Through Postgres `resolve()`, all 199 gold chunks match their pinned hashes and all 160 hard negatives resolve.
- The Spec reviewer spot-checked 5 drafts against their chunks, and every gold answer was supported. All 30 multi_hop records use 2 or more filings; all 15 global records span all three companies.
- Tests: 822 passed, 1 skipped, with the database. Coverage on `eval/` is 94%. ruff is clean, and pyright reports 0 errors.

**Review catches**
- **python-review, HIGH, all fixed test-first:**
  - `human_verified` wasn't tied to any review. It now needs `derived_from` and an accept in the log.
  - The review log was keyed by draft ID, which a re-merge renumbers. It's now keyed by `draft_key`.
  - Review could corrupt the human set: duplicate IDs, a crash between the two writes, or one bad log line. New IDs now follow the highest existing ID, decisions are rebuilt from the log plus `derived_from`, and bad log lines are skipped with a warning.
  - Cache writes used a fixed temporary file name, a race between parallel searches, and a malformed cache could crash a search. Each write now uses a unique temporary file, and a malformed cache counts as a miss.
- **python-review, MEDIUM/LOW, fixed:**
  - decline and unanswerable records with hard negatives;
  - repeated hard negatives;
  - unchecked split, class-vs-scope and author-vs-provenance rules;
  - a bare `assert` in search;
  - clean CLI errors;
  - pyright errors from Optional values.
- **Spec review:**
  - The plan's `--db` mode was missing; it's now built and tested.
  - Nothing enforced the minimum of one hard negative; the validator now does.
  - The human set was chosen only by file name; `--kind` now overrides that.
  - The plan named `eval.show`; it was built as `eval.search --show`, and the plan is updated.
  - The highest risk: `echo a | eval.review` could mint `human_verified` records. It now needs an interactive terminal, and CLAUDE.md forbids agents from running it.
- **Standards review:**
  - Silently skipped errors are now logged.
  - The merge and coverage commands fail cleanly.
  - `SKIP` and `QUIT` are named constants.
  - There's one shared `normalise_question`.
  - Hard-negative relations have explicit branches.
  - Not changed, all judgement calls: the seven-parameter `review()`, and the class lists kept in three places.

**Parsing issues the drafters found** (not fixed; they need a parser look)
- Intel's FY2025 10-K headcount figure and capex table couldn't be found in any chunk. That may be content lost in parsing; among the 47 tables edgartools renders empty in the 24-filing manifest is a candidate.
- In an NVIDIA Q3 FY2026 10-Q table, both column headers read "Oct 27, 2024". It looks like edgartools combining header rows wrongly.
- Later filings repeat earlier figures in comparison columns, so some hard negatives also contain the gold value. The drafters flagged these in each record's notes.

**What the user still has to do**
1. Superseded the same day; see the follow-up entry below.
2. The friend test: someone tries five dev multi-hop questions with Ctrl-F.
3. The grilling session on `.claude/plans/step-4-grilling.md`: the hypotheses in `.claude/plans/research-plan-draft.md`, and moving the plan to `docs/` past the ECC hook.

**Next session starts with**
1. If the user has reviewed drafts, run `uv run python -m eval.validate eval/eval_set.jsonl --db` and the coverage report on the human set.
2. The grilling session, then `docs/RESEARCH-PLAN.md` fixed and dated before any benchmark run.
3. Step 5 (vector baseline and eval harness) only after the human set reaches 120 and the research plan is approved. First, the deferred ECC upgrade.

---

## 2026-10-04 — Step 4 closed on the agent-drafted set (user decision)

- **What happened.** The user ran `python -m eval.review`. All 140 drafts were accepted in 1 minute 44 seconds (from the review log's timestamps), under a second each, so they weren't checked against their gold text. Keeping them would have labelled 140 unchecked records `human_verified` under the user's name.
- **Options given.** The assistant raised this and offered two options: review again properly, or use the drafts as `agent_drafted`. Agents may not write or delete the human set or the review log, so the user removed `eval/eval_set.jsonl` and `eval/review_log.jsonl` themselves. Neither was ever committed.
- **The decision.** The user chose to run the benchmark on the 140 agent-drafted questions without human review. It is recorded in CLAUDE.md Decisions, the RUNBOOK Step 4 amendment, OPEN-DECISIONS, the research-plan draft (eval set and threats to validity) and the README.
- **Consequences.**
  - The RUNBOOK stop condition (120 human entries) is replaced, not met.
  - Every result must say "agent-drafted questions".
  - The drafts' unchecked reviewer notes are listed as a threat to validity.

**Next session starts with**
1. The grilling session on `.claude/plans/step-4-grilling.md`. Item 3 there is now settled. Item 4 (review cadence) is moot unless the user reviews later. Approving the hypotheses is still open.
2. The friend Ctrl-F test on five dev multi-hop drafts (optional).
3. The deferred ECC upgrade, then Step 5 (vector baseline and eval harness), with `docs/RESEARCH-PLAN.md` fixed and dated before any benchmark run.

---

## 2026-10-04 — Step 4 grilling session: research plan approved

- **The research plan is fixed.** It moved from `.claude/plans/research-plan-draft.md` to `docs/RESEARCH-PLAN.md`, dated and approved before any benchmark run. The thresholds stay as drafted.
- **The sample-size problem, found before approval.** The `test` split holds only 4 cross-company multi-hop questions and 5 global ones. With so few, a 0.10 difference can't produce an interval that excludes 0, so H2 and H4 would have come out inconclusive whatever happened. The user chose:
  - H2 covers all 7 `multi_hop` test questions, with cross-company as a secondary breakdown;
  - any class with fewer than 10 `test` questions is reported as "directional", and counts as confirmed only when its interval excludes 0.
- **H6 now has a fixed class-to-arm map**, so router accuracy can't be fitted to the results afterwards.
- **Other decisions.**
  - The 20 decline and unanswerable questions sit outside the RUNBOOK's 120.
  - The FinRank corpus check is approved as an appendix result after the vector baseline.
  - The friend Ctrl-F test was skipped and is listed as a threat to validity.
- **The ECC hook.** The hook that blocks new .md files is in the ECC 1.4.1 plugin cache, and the planned upgrade would overwrite an edit there. The file moved with `git mv` instead, and `docs/` gets added to the allowlist after the upgrade.
- **Setup.** The user chose to install Matt Pocock's `grilling` skill (the one `grill-me` calls) instead of removing `grill-me`, to do the ECC upgrade before Step 5, and to switch models in the app.
- Recorded in CLAUDE.md Decisions, OPEN-DECISIONS, the RUNBOOK Step 4 amendment and the outcome section of `.claude/plans/step-4-grilling.md`.

**Next session starts with**: superseded by the entry below.

---

## 2026-10-04 — ECC removed; Matt Pocock's skills run the workflow

- **Why the ECC upgrade stopped.** ECC 2.2.3 was installed as `ecc@ecc` with the minimal hook profile. `claude plugin details` then showed two problems:
  - it adds about 44,800 tokens to every session (387 skills, 68 agents);
  - it bundles a `chrome-devtools` MCP server that runs `npx -y chrome-devtools-mcp@1.10.1`.
- **What was done instead.** The user chose to remove ECC and adopt Matt Pocock's skills for the whole workflow.
  - Both ECC plugins and their marketplaces were uninstalled.
  - The 58 leftover 1.x skill copies were moved, not deleted, to `~/.claude/backup-ecc-1.4.1/skills/`.
  - `mattpocock-skills` was installed from the official marketplace (v1.2.3, MIT, 25 skills). It adds about 1,600 tokens per session, has no hooks and no MCP servers.
- **The repo copies of `grill-me` and `grilling` were removed**, because the plugin provides both and Matt's docs warn that two install routes duplicate every skill.
- **The workflow (CLAUDE.md).** Each RUNBOOK step goes `/grill-with-docs` → `/to-spec` → `/to-tickets` → `/implement` (which drives `/tdd` and ends with `/code-review`), then ruff, pytest and the eval slice, then a BUILD-LOG entry.
  - `/ask-matt` routes tasks that don't fit.
  - Hard bugs go through `/diagnosing-bugs`, and `/research` replaces search-first.
- **Setup (Matt's `setup-matt-pocock-skills`, applied by hand).**
  - The issue tracker is local markdown under `.scratch/`, committed.
  - Triage labels are the defaults.
  - Domain docs are single-context (CONTEXT.md and docs/adr/, created when first needed).
  - Config is in `docs/agents/`.
- **Records are kept as before.** RUNBOOK, BUILD-LOG, OPEN-DECISIONS and CLAUDE.md Decisions are unchanged in role. New ADRs get a one-line pointer in Decisions. `.claude/plans/` and `.claude/skills/step-review` stay as history.
- **The .md hook question is moot.** It was part of ECC, so there's no hook blocking new docs any more.

**Next session starts with**
1. Restart the session so the plugin changes load. Check that `/ask-matt` and `/grill-with-docs` are listed and that no `everything-claude-code` or `ecc` skills remain.
2. The model switch, in the app's model picker (the user's setting).
3. Step 5 (vector baseline and eval harness) via `/grill-with-docs`, against `docs/RESEARCH-PLAN.md`.

---

## 2026-10-05 — Step 5 ticket 02: chunk embeddings in Postgres (code done; needs a Voyage key to run)

- **What exists now.**
  - `chunk_embeddings` (db/schema.sql): keyed on `(chunk_id, model)`, with `dimensions`, `text_sha256`, `api_token_count`, an untyped `vector` with a `vector_dims = dimensions` CHECK, and `embedded_at`. No ANN index. `apply_schema()` checks its live columns. `chunks` is unchanged.
  - `ingest/voyage.py`: a Voyage client over httpx. It sends input type `document` with truncation off, reads the key from `VOYAGE_API_KEY`, validates every response (model, count, 1,024 finite dimensions, positive token count), and backs off on 429 and 5xx, honouring `Retry-After`.
  - `ingest/embed.py` (`python -m ingest.embed`): embeds chunks with no current vector, one chunk per request, and commits after each. A rerun skips chunks whose stored text hash still matches.
  - `store.chunks_for_embedding` and `store.save_embedding` hold the SQL.
- **Deviations, each in the ticket's comments.**
  - Batch size is 1, because Voyage reports token usage per request, not per input.
  - The check before any call uses cl100k counts with a 2x margin under the 32k context (16,000). That margin is an assumption; the largest chunk is 1,801.
  - The Voyage response in `tests/fixtures/voyage/` is a labelled placeholder: no key was set, so no real response could be recorded. `scripts/capture_voyage_response.py` replaces it.
- **Evidence.** Tests: 842 passed, 1 skipped (the old documented-pointer skip), with 20 new in `tests/test_embed.py`. `ruff` is clean. Three hand-made mutations to the client (truncation on, no 429 retry, no dimension check) each failed a test, and so did ignoring `Retry-After`. The dev database took the schema: 2,144 chunks wait to be embedded.
- **Review (`mattpocock-skills:code-review`).**
  - Standards findings fixed: nesting in the retry loop, hardcoded values, mutable module maps (now a frozen `ModelSpec` and `MappingProxyType`), an HTTP client that was never closed, `Retry-After`, embedding SQL living outside `store.py`, rows unpacked by position, and the capture script copying the request.
  - Not done: an index on `model`, because search is a sequential scan over about 2,000 rows (noted in the schema).
  - Spec findings: the fixture test was loosened to allow extra fields in a real response; batch size, the margin and the cascade went to the ticket and OPEN-DECISIONS.
- **TDD note.** The store-side behaviour (one row per chunk, rerun skip, stale re-embed, oversized failure, the CLI) was test-first, each test seen failing. The client's request, retry and validation code was written in the first slice, before its tests. Those tests were then checked by mutation.
- **Not run.** `python -m ingest.embed` itself, which needs the key and the network.

- **Later the same day.** The user added the key and recorded a real Voyage response, which replaced the placeholder (1,024 dimensions, 25 tokens). `tests/test_embed.py` passes on it: 20 passed.
- **The corpus is embedded.** The first run crawled at about 3 chunks a minute: the Voyage account had no payment method, which keeps it on low rate limits. Retries printed nothing, so the run looked frozen; each retry is now logged (8047835). After the user added a payment method, the rerun finished: `embedded 1843, skipped 301 current, 1298189 API tokens`.
- **Database checks after the run.**
  - 2,144 chunks and 2,144 `voyage-4-large` rows, one per chunk, all 1,024 dimensions.
  - 0 rows whose text hash differs from the chunk text; 102 preamble chunks embedded; every vector has norm 1.0.
  - About 1.5M API tokens across all runs, inside the 200M free tokens.
- **The 2x length margin, measured.** Voyage's count over the cl100k count is 1.06 on average and 1.27 at most (correlation 0.98). Voyage per-chunk counts run from 4 to 2,106.

**Next session starts with**
1. Settle the two ticket 02 items in OPEN-DECISIONS, then `/implement .scratch/step-5/issues/03-retrieval-only-scored-run.md`.

---

## 2026-10-05 — Step 5 ticket 03: a retrieval-only scored run

- **Built (6be335b).** `python -m eval.run --arm vector` embeds each question with input type `query` and caches the vector on disk, keyed by model and text hash (`data/cache/query_embeddings/`). It retrieves the top 10 chunks by exact cosine similarity with no filter, scores recall and precision at 5 and 10 from chunk IDs, and writes `benchmarks/runs/{date}-{arm}-{split}-{commit}.json`. Git ignores that directory.
  - One arm interface (`retrieve/arm.py`) and a registry (`retrieve/arms.py`). The harness passes only the question text to the arm (ADR-0001).
  - `dev` is the default split. `test` needs `--final`, and `--final` without `test` is refused. Missing env vars stop the run before the arm opens.
  - `eval_set.jsonl` is read only when it has records, and its results go in their own column. Each file must hold only records of its own provenance, or the run stops.
  - The vector arm refuses to start if any chunk lacks a current vector. Postgres recomputes the text hash for this check.
  - Latency is recorded as query embedding plus search, with a cache-hit flag. Otherwise cached reruns would report near-zero embedding time and skew p95 (Spec review finding).
  - `git_state()` now also treats untracked files in `eval/`, `retrieve/` and `prompts/` as dirty. Without this, a run that used a new uncommitted harness module would claim a clean commit.
- **Definitions.** recall@k = gold chunks in the top k / gold chunks. precision@k = gold chunks in the top k / k, even when fewer than k come back. Both definitions are written into each results header.
- **First run, retrieval only (agent-drafted questions, dev, commit 6be335b, voyage-4-large, k = 10, chunker_version 1, no filter, eval set SHA-256 cd536eea22d5…).**

  | class | n | recall@5 | precision@5 | recall@10 | precision@10 |
  |---|---|---|---|---|---|
  | lookup | 27 | 0.704 | 0.141 | 0.889 | 0.089 |
  | local | 22 | 0.795 | 0.182 | 0.932 | 0.109 |
  | multi_hop | 23 | 0.478 | 0.200 | 0.616 | 0.130 |
  | global | 10 | 0.325 | 0.200 | 0.483 | 0.150 |
  | decline | 6 | — | — | — | — |
  | unanswerable | 5 | — | — | — | — |

  This run has no filter and no answers, and the numbers have no intervals yet, so it is not the baseline. Multi-hop recall@10 is 0.62 against 0.89 to 0.93 on lookup and local. Search takes about 8 ms (p50). The first uncached run took 33 s for 93 questions, mostly Voyage calls; a rerun makes none.
- **Evidence.** 878 passed, 1 skipped (the old pointer skip). `ruff` is clean. New tests: `test_eval_metrics.py` (hand-computed values on q0113 and q0028), `test_query_cache.py`, `test_vector_arm.py` (test schema, vectors at known angles), `test_eval_run.py` (fake arm) and two provenance tests on a throwaway git repo. Each new test was seen failing first.
- **Review (`mattpocock-skills:code-review`).**
  - Standards findings fixed: an unhandled write error after the run (now exit 3 with a message), a copied SHA-256 helper, the arm name written twice, the eval file read twice (the hash and the records now come from one read), a mutation-style loop in `score`, and an inline import in a test.
  - Not changed: the corpus check stays in `VectorArm.__init__`, because failing before the first question is the point. `_run_arm` keeps a local list because it logs progress as it goes. `results_path` keeps its parameters because the date isn't part of `RunInfo`.
  - Spec findings: the latency split above was done. The join to `chunks` is left for ticket 04, which needs it for the filter. The vector arm requires `VOYAGE_API_KEY` and `DATABASE_URL`; ticket 05 must add `ANTHROPIC_API_KEY` to its `required_env`, as the Step 5 harness decision says.
- **Decision for the user.** The CLAUDE.md line on `git_state()` still lists only `ingest/`, `db/`, `tests/` and `scripts/`. Recommendation: update it to include `eval/`, `retrieve/` and `prompts/`, as the code now does.

**Next session starts with**
1. `/implement .scratch/step-5/issues/04-question-filter.md`.

## 2026-10-05 — Step 5 ticket 04: the question filter

- **Built.** `retrieve/question_filter.py` holds the ADR-0001 parser. `parse_question_filter(question, filings)` is a pure function of the question and the corpus filing list. It returns the companies named, the fiscal periods that selected a filing, the forms named, and the accession numbers retrieval may use (`None` means no filter).
  - The parser resolves to accession numbers per company because the rules differ by company. Calendar quarters apply to AMD and Intel only, and "latest 10-K" means each company's own newest 10-K.
  - The vector arm reads the filing list from Postgres once at start and filters in SQL with `c.accession_no = ANY(%s::text[])`, a bound parameter. An empty list is refused rather than searching everything.
  - Each results file has a `filter_report` per question set: exact match against the labels (companies and periods), the two parts separately, filter-excluded gold, and the questions that lost gold. Each question record shows its filter, whether it matched the labels, any excluded gold, and the named companies no retrieved chunk came from (multi-company questions only).
  - `tests/fixtures/corpus_filings.json` holds the 24 filings' metadata (no text), built from `data/parsed` by `scripts/build_fixtures.py`, so the parser tests run in CI. A test checks it against `data/parsed` when that folder exists.
- **Rules added beyond ADR-0001's text, each following its "no filter when unsure" rule.** A period with no filing in the corpus (fiscal 2021, NVIDIA fiscal 2024) is ignored. A fourth quarter, which has no 10-Q, leaves its company with no period filter. A list sharing one year or quarter word ("fiscal 2024 and 2025", "Q1 and Q2 of fiscal 2025") sets no period. A filter that selects nothing is no filter.
- **Dev run (agent-drafted questions, dev, commit 3418218+dirty, i.e. this change before its commit; voyage-4-large, k = 10, chunker_version 1, eval set SHA-256 cd536eea22d5…).** Filter-excluded gold is 0. Exact match is 0.806 (companies 0.989, periods 0.817). Every miss sets less than the labels: a bare year ("its 2025 earnings"), "each company" with no names, or a list.

  | class | n | recall@5 | precision@5 | recall@10 | precision@10 | recall@10, ticket 03 (no filter) |
  |---|---|---|---|---|---|---|
  | lookup | 27 | 0.889 | 0.178 | 1.000 | 0.100 | 0.889 |
  | local | 22 | 0.932 | 0.218 | 0.977 | 0.118 | 0.932 |
  | multi_hop | 23 | 0.616 | 0.261 | 0.717 | 0.157 | 0.616 |
  | global | 10 | 0.592 | 0.380 | 0.800 | 0.260 | 0.483 |

  This is still retrieval only, with no intervals, so it is not the baseline. Three multi-company questions had a named company with no retrieved chunk: q0022 (NVIDIA), q0124 and q0125 (AMD).
- **Evidence.** 943 passed, 1 skipped (the old pointer skip). `ruff` is clean. New tests: `test_question_filter.py` (60, one per ADR rule plus the dev excluded-gold check), three vector-arm tests on the test schema, three harness tests. Each was seen failing first.
- **Review (`mattpocock-skills:code-review`).**
  - Standards, no hard violations. Fixed: the CIK-to-ticker map copied three times (now `ingest.corpus.TICKER_BY_CIK`), chunk-ID parsing copied twice (now `ingest.chunker.accession_of`), positional row unpacking (now a `FilingRow` NamedTuple), exact match computed in two places (now `filter_report.exact_match`), the name `mine`, a bare 24 in a test, and in-loop rebinding in the parser. `_take` still blanks text in a loop over three patterns, because a fold would read worse.
  - Spec findings fixed: shared-year lists and a fourth quarter beside another period both narrowed the filter and cut out evidence. Dev has no question worded either way, so the dev check could not catch them. Both now set no period, with tests.
  - Spec findings not changed: "latest" applies to every company in scope, so "NVIDIA's latest 10-K and AMD's most recent 10-Q" also allows NVIDIA's latest 10-Q. It filters more loosely than the question, never more tightly. Companies without chunks is reported only when the question names two or more companies, as the ticket says. q0026 ("each company") names none, so it gets nothing. A label-based version would belong in the harness.
- **Decisions for the user (settled 2026-10-05: as recommended; recorded in ADR-0001 Refinements and CLAUDE.md Decisions).**
  1. Record the four extra filter rules above. Recommendation: a dated "Refinements" section in ADR-0001, since they extend its rules.
  2. Add a CLAUDE.md Decisions line for the metadata fixture. Recommendation: one line saying `tests/fixtures/corpus_filings.json` is generated from `data/parsed` and holds metadata only, so the "real filings" fixture rule still holds.

**Next session starts with**
1. `/implement .scratch/step-5/issues/05-cited-answers.md`. Ticket 05 must add `ANTHROPIC_API_KEY` to the vector arm's `required_env`.

---

## 2026-10-05 — Step 5 ticket 05: cited answers (code done; needs an Anthropic key to run)

- **Built.** The vector arm now writes an answer. All 10 retrieved chunks go to `claude-sonnet-5-5` at effort `high`, with prompt v1, and a deterministic step keeps only cited sentences.
  - `prompts/answer/v1.md` holds the system text and the user template. Each chunk is shown with its ID, company, form, fiscal period and section. `retrieve/answer_prompt.py` pins v1's SHA-256 and refuses an edited v1, so a change has to become v2.
  - The reply starts with a status line: `answered`, `declined` or `not_found`. A decline is shown as a fixed sentence; a not-found answer is a fixed sentence followed by any cited sentences that survived. A reply with no status line is enforced as an answer and labelled `no_status`, and an API refusal is labelled `refused`.
  - `retrieve/citations.py` splits each line into sentences: a sentence ends at `.`, `!` or `?` followed by whitespace, and not after `Inc.`, `vs.` or initials such as `U.S.`. Citations straight after a full stop belong to the sentence before it. A sentence survives only if it cites a chunk and every chunk it cites was retrieved. Dropped sentences stay in the record with their reason (`no_citation` or `citation_not_retrieved`).
  - `retrieve/answer_model.py` calls `messages.create` with model, `max_tokens` 16,000, system, one user message and `output_config.effort`, and nothing else. SDK 1.11 has no temperature, top_p or top_k parameters at all. No refusal fallback is configured, so no other model can write an answer.
  - `retrieve/response_cache.py` stores every response under `data/cache/responses/{model}/{effort}/{request SHA-256}.json`, with the original call's latency. A rerun makes no answer calls. The key covers the whole request, so changing `max_tokens` also misses the cache.
  - `retrieve/pricing.py` holds the price table, dated 2026-10-05: Sonnet 5.5 $2 / $10, Opus 5.5 $4 / $20 (cache reads $0.20, which is 0.05x on Opus 5.5), voyage-4-large $0.12, all per million tokens, from each vendor's pricing page. The arm refuses to start if a model it uses has no price.
  - Results: each question record has the answer, the raw reply, kept and dropped sentences with their citations, status, stop reason, token usage, generation latency and cost by stage. Each cell adds status counts, dropped sentences by reason, structural citation validity (cited IDs that were retrieved / cited IDs, over the raw answer), p50/p95 latency for retrieval, embed, search and generation, and mean cost per query. The header adds efforts, the prompt version, hash and path, `max_tokens` and the price table.
  - `ANTHROPIC_API_KEY` is in `.env.example` and in the vector arm's `required_env`. Without it, `python -m eval.run --arm vector` stops with exit 2 before opening anything (checked).
- **Recorded responses are placeholders.** No Anthropic key is set, so `tests/fixtures/anthropic/` holds three hand-built bodies in the API shape, validated by the SDK's `Message` model and labelled in their `recorded` field and README. `scripts/capture_anthropic_responses.py` replaces them with real responses for q0072 (answer), q0005 (decline) and q0011 (not found). That ticket check stays open until it runs.
- **Evidence.** 1009 passed, 1 skipped (the old pointer skip). `ruff` is clean. New tests: `test_citations.py` (12), `test_answer_prompt.py` (6), `test_answer_model.py` (19), `test_answer.py` (11), `test_eval_operational.py` (3), `test_atomic_json.py` (2), 8 in `test_vector_arm.py` and 4 in `test_eval_run.py`. Citations, prompt, answer model, answer, arm and harness tests were each seen failing first. The percentile edge cases and the atomic-write test were written after the code.
- **Review (`mattpocock-skills:code-review`).**
  - Standards findings fixed: an unpriced model, a chunk from an unknown filing or a failed cache write escaped the arm as something other than `ArmError`; sentence merging mutated a list (now a `reduce` over tuples); the atomic writer left a temp file on failure and lived in `parsed_files.py` (now `ingest/atomic_json.py`, tested); `write_answer` had unused model and effort parameters; `refused` was tested twice; `ArmConfig` had `None` defaults no arm uses; usage was built by hand instead of `asdict`; a test helper's argument was named `r`; the capture script had no write or empty-response checks.
  - Not changed: the Anthropic and Voyage clients share a shape (`from_env`, context manager), and so do the two caches' read checks. Merging them would couple two vendor clients for a few lines each. `ArmResult` keeps its flat fields, which tickets 03 and 04 already use. Costs stay plain dicts because they are written to JSON as-is.
  - Spec findings fixed: a retrieval latency stage (embed plus search, fresh query embeddings only), an arm-level not-found test, and a note that the cache key includes `max_tokens`.
  - Spec finding rejected: the reviewer read Opus 5.5's $0.20 cache read as a mistake. The pricing page gives 0.05x for Opus 5.5.
- **Decision for the user.** The fixed decline and not-found sentences carry no citation. Invariant 3 says uncited sentences are dropped. The decline sentence makes no claim about the filings. The not-found sentence makes a claim about what was retrieved, not about the filings' content. Recommendation: accept both as system statements and add a CLAUDE.md Decisions line saying so, so the exception is on record. Settled 2026-10-05, as recommended; recorded in CLAUDE.md Decisions.

- **Later the same day.** The user added an API key (a plain key; workload identity federation was considered and left for Step 6, in case CI ever calls the API live) and recorded real responses.
  - q0072: answered, 2 sentences, both kept, giving 22% and 14% as in the gold answer. 12,378 input and 158 output tokens, $0.026, 3.7 s.
  - q0005: the status line `declined` alone, 9 output tokens.
  - q0011: not_found, 3 sentences, 1 dropped. The dropped one ("The excerpts do not name any customer, including Microsoft…") has no citation, so the drop follows the rule. The two kept sentences cite the right chunks.
  - None of the three responses has a thinking block.
  - Four tests had hard-coded the placeholders' numbers. They now take their expected values from the recording; each checks the same behaviour as before. 1009 passed, 1 skipped.

**Next session starts with**
1. `/implement .scratch/step-5/issues/06-wrong-evidence-and-statistics.md`.

## 2026-10-05 — Step 5 ticket 06: wrong evidence and statistics

- **What changed.** Every results file now reports a wrong-evidence rate, and every cell metric has a bootstrap 95% interval. Cells with fewer than 10 scored questions are labelled `directional`.
  - `eval/wrong_evidence.py`: a hard negative counts as wrong evidence when it ranks above at least one gold chunk, or when it is cited in any sentence of the raw answer, kept or dropped. A gold chunk that was not retrieved ranks below every retrieved chunk. Each cell gives the rate over questions with hard negatives, then per label: the rate over questions with a negative of that label, plus that label's share of the wrong questions (for H3). Each question record lists the hard negatives that triggered, with rank, `above_gold` and `cited`.
  - `eval/bootstrap.py`: a percentile bootstrap with 10,000 resamples. Each interval's generator is seeded from the header seed plus the interval's set, arm, class and metric, so adding a cell or a metric leaves every other interval unchanged. Intervals cover recall and precision at 5 and 10, the wrong-evidence rates and label shares, dropped share, structural citation validity, p50 and p95 latency per stage, and mean cost per query. A ratio's interval resamples numerator and denominator together. The header records the seed, the resample count and the confidence level.
  - `percentile` moved from `eval/operational.py` to `eval/bootstrap.py`, because `operational` now needs intervals and the two modules would otherwise import each other.
- **Evidence.** 1034 passed, 1 skipped. `ruff` is clean. New tests: `test_wrong_evidence.py` (8) and `test_bootstrap.py` (10), on real records q0072, q0113, q0028 and q0004, plus 7 in `test_eval_run.py`. Each new test was seen failing before its code existed. In the fake run, q0073's unretrieved citation is now its own hard negative, so a cited-only case is covered; its sentence and citation counts are unchanged. Two older tests compared whole latency and cost dicts. They now leave out the new `intervals` key and check the same values as before. At dev scale the intervals add about 3 s to a run.
- **Review (`mattpocock-skills:code-review`).**
  - Standards findings fixed:
    - the threshold 10 was written in two modules (it is now one constant, used in the definition text too);
    - the generator type had no name (now `RngFor`);
    - `wrong_evidence.wrong_evidence` repeated its module's name (now `wrong_evidence_hits`);
    - two loops filled local lists (now comprehensions).
  - Standards findings not changed:
    - hits are computed twice per question, once for the cell and once for the record. The function is cheap and deterministic, and caching it would add state to `Outcome`.
    - `hit_record` stays as a thin wrapper, matching `answer_record`, to keep one place that sets the output shape.
  - Spec findings fixed:
    - latency and cost had no intervals, though the spec asks for one "on every cell";
    - H3 needs each label's share of the wrong questions, which is now reported.
- **Decision for the user.** What "ranks above a gold chunk" means when a question has several gold chunks.
  - Current reading: above at least one gold chunk, with an unretrieved gold chunk ranked last. On a multi_hop question that misses one gold chunk, any retrieved hard negative counts.
  - Stricter reading: above the best-ranked gold chunk. This counts only negatives the model sees before any gold chunk.
  - Recommendation: keep the current reading. A confusable chunk retrieved while gold evidence is missing is the failure H3 is about. Each question record stores ranks, so the stricter rate can be recomputed from the results file and reported beside it if needed. Record the choice as a dated clarification in docs/RESEARCH-PLAN.md. Settled 2026-10-05, as recommended; recorded in docs/RESEARCH-PLAN.md Amendments and CLAUDE.md Decisions.

**Next session starts with**
1. `/implement .scratch/step-5/issues/07-judged-metrics.md`.

## 2026-10-05 — Step 5 ticket 07: judged metrics

- **What changed.** The harness now runs LLM judges on every scored answer, three times each, after the arm has answered every question.
  - `eval/judging/claude.py`: `ClaudeJudge` subclasses Ragas's `InstructorBaseRagasLLM`. It calls `claude-opus-5-5` at effort `medium` through `messages.create` with `output_config.format` (a JSON schema built by `anthropic.transform_schema`), with no sampling settings. It costs each call from the reported usage, including calls whose reply can't be used. The research note proposed `messages.parse`. `create` sends the same `output_config` (the SDK's `parse` merges effort and format the same way) and returns the raw body the response cache already stores.
  - Every judge request goes through the existing response cache (`ModelRequest` protocol, shared with answer requests). The cache key covers the run number (1 to 3) and a repeat counter, so the three runs are independent and Ragas answer relevancy's three identical prompts get three answers.
  - `eval/judging/embedder.py`: a `BaseRagasEmbedding` around the cached Voyage query embedder, for answer relevancy.
  - `eval/judging/scoring.py`: the answerable classes get Ragas faithfulness (on the kept sentences, citation markers removed, against all retrieved chunks), Ragas answer relevancy (on the shown answer) and citation support (the project's judge, one verdict per kept sentence). Decline records get `decline_correct` and unanswerable records get `not_found_correct`, so those rows hold only those metrics. A reply the judge can't use (a refusal, a truncated reply, or JSON that doesn't match the schema) is recorded against its metric. Any other failure stops the run with no results file.
  - `eval/judging/report.py`: each question gets the per-run values, mean and spread. Each cell gets the mean over questions, the mean within each run, the spread of the run means, a bootstrap interval, an error count and the judging cost, all labelled `uncalibrated`. Judging cost is kept out of the operational cost per query.
  - The header records the judge model, effort, max tokens, runs, Ragas version, relevancy embedding and strictness, and the SHA-256 of both project judge prompts.
  - `eval/judging/__init__.py` forces `RAGAS_DO_NOT_TRACK=true` before ragas is imported. CI sets it too.
  - Judge jobs run on 8 threads. Each (question, run) job has its own judge object, so cache keys don't depend on job order.
  - `ArmResult` now carries `sources`, the chunks the answer model saw, so the judges see the same text.
- **Dependencies.** `ragas==0.4.3` (approved). ragas 0.4.3 imports `langchain_community.chat_models.vertexai`, which langchain-community 0.4.2 removed, so `pyproject.toml` caps it with `[tool.uv] constraint-dependencies = ["langchain-community<0.4.2"]` (user approved, 2026-10-05).
- **Live check.** `scripts/capture_judge_responses.py` ran judge run 1 on the three recorded answers (8 Opus calls, about $0.12). Opus accepted effort `medium` together with `output_config.format`, and every reply parsed against the Ragas and project schemas. Scores: q0072 faithfulness 1.0, relevancy 0.971, citation support 1.0; q0005 decline correct; q0011 not-found correct. The first capture sorted the schema's keys, and Ragas's faithfulness verdicts came back with empty `reason` fields, because the model writes fields in schema order and `reason` no longer came first. The schema now keeps each model's declared order, and a recapture gave full reasons (492 output tokens, up from 65). The cache files are committed as test fixtures in `tests/fixtures/judge/`.
- **Evidence.** 1079 passed, 1 skipped, offline. `ruff` is clean. New tests:
  - `test_judge_claude.py` (12): request shape, cache keys per run and repeat, unusable replies;
  - `test_judge_scoring.py` (14): real Ragas metrics against a scripted judge, every score hand-computed;
  - `test_judge_replay.py` (4): the real judges replay the recorded Opus and Voyage responses with both backends refusing calls;
  - `test_ragas_offline.py` (1): imports ragas and scores an answer in a fresh interpreter whose sockets fail, with an inherited `RAGAS_DO_NOT_TRACK=false` overridden;
  - `test_judge_runner.py` (4);
  - 8 harness tests in `test_eval_run.py`.
- **Review (`mattpocock-skills:code-review`).**
  - Standards findings fixed:
    - judge failures other than API, Voyage and cache errors now stop the run cleanly instead of with a traceback;
    - the report no longer assumes every question has all three runs;
    - the definitions interpolate `JUDGE_RUNS` and `RELEVANCY_STRICTNESS`;
    - `Effort` and `Class` types replace bare strings;
    - the duplicated cost and call totals now come from one helper;
    - in the capture script: types, an explicit encoding, and a clear message when `DATABASE_URL` is unset.
  - Standards findings not changed:
    - `ClaudeJudge` and the embedder keep per-run counters as attributes, because Ragas calls them through its own interface and reads only the parsed reply. Both objects are short-lived, and the counters are replaced with new copies, never changed in place.
    - `AnswerModel` and `CachedAnswerModel` keep their names although they now also serve judge requests. Renaming them touches every Step 5 module for no change in behaviour.
    - The package `__init__` setting an environment variable is the point of the module.
    - Three runs of one question embedding the same text can each call Voyage. This can't happen in practice: the arm has already cached the question's embedding, and the generated questions differ between runs.
  - Spec findings fixed: the definitions now separate judging cost from the operational cost per query.
  - Spec findings raised with the user: the langchain-community cap, and faithfulness on the shown sentences rather than the raw answer. Both settled 2026-10-05, as recommended.
- **Cost note for ticket 08.** About 18 Opus calls per answerable question and 3 per decline or unanswerable question. On the live check that was about $0.10 per answerable question across three runs, so roughly $10 to $15 for the dev split. Reruns replay from the cache.

**Next session starts with**
1. `/implement .scratch/step-5/issues/08-baseline-run.md`.


## 2026-10-05 — Step 5 ticket 09: the judge moves to gpt-6-luna

- **What changed.** Every judged metric now scores through `gpt-6-luna` on OpenAI's Responses API. The Opus judge class, its tests, its fixtures and the Opus price row are deleted; the Sonnet answer model is untouched. New modules:
  - `eval/judging/openai_backend.py`: `OpenAIResponsesModel` (behind the existing response cache), the reply parser and token usage;
  - `eval/judging/openai_judge.py`: `OpenAIJudge`, a Ragas `InstructorBaseRagasLLM` subclass.
- **The request.** A strict `json_schema` text format built by the SDK's strict transform, `reasoning.effort` `medium`, `max_output_tokens` 25,000, `store=false`, and no `temperature`, `top_p` or `seed`. Schema fields keep their declared order, so reasons come before verdicts.
- **Failures.**
  - A refusal, an `incomplete` reply or JSON that fails the schema raises `JudgeReplyError`, recorded against that one metric.
  - A `failed` or unfinished response, an API or network error, or a cache error stops the run.
  - Authentication errors never quote the API's message, because it echoes part of the key.
- **Keys.** `eval.run` checks `OPENAI_API_KEY` at start and refuses to start while `OPENAI_BASE_URL` is set; `from_env` refuses it too and pins the address to `https://api.openai.com/v1`. `.env.example` lists an empty `OPENAI_API_KEY`.
- **Cache keys.** OpenAI judge keys hash `"provider": "openai"` with the request. The Anthropic answer key is unchanged: a test pins its pre-change hash, and the recorded answer fixtures still replay.
- **Cost and usage.** `openai_cost` bills cached input at the cached rate and reasoning as output. The price table has dated rows for `gpt-6-luna` ($0.10 / $0.01 cached / $0.125 cache write / $0.50 per MTok) and `gpt-6-sol` ($2.00 / $0.20 / $2.50 / $10.00), read from OpenAI's pricing page on 2026-10-05. Judge tokens are reported per question, per cell and for the whole run (`judge_usage` in the results file), with reasoning tokens apart. The header records provider, model, effort, output cap and the openai SDK version.
- **Dependency.** `openai==3.3.0` is a direct dependency (user-approved). The lock gained two lines and changed no versions.
- **First live call.** `scripts/capture_judge_responses.py` recorded judge run 1 on the three recorded answers (8 luna calls, about $0.002 in all). openai 3.3.0 works with `gpt-6-luna`. On q0072, luna used 472 reasoning tokens of 1,142 output tokens across six calls, far below the 25,000 cap; the two verdict calls used 0 and 27. Scores: q0072 faithfulness 0.8, relevancy 0.955, citation support 1.0 (Opus: 1.0, 0.971, 1.0); q0005 decline correct; q0011 not-found correct. Luna marked one of five statements unsupported where Opus accepted all five. That is one question, not a finding; ticket 10's spot check is the comparison.
- **Problems met.**
  - The first capture failed to replay: `model_dump` writes the request format's `schema` field as `schema_`, so the stored body no longer validated. The backend now stores `to_dict()`, which keeps the API's names, and a test checks the round trip.
  - Docker Desktop could not start (a second copy was running from the mounted installer image during an update). Postgres was down, so the capture script now reuses the committed `inputs.json` and queries Postgres only when it is missing. The script also scans the recorded files for key and organisation ID patterns and fails if one appears.
  - The first OpenAI key in `.env` was rejected (HTTP 401); the user replaced it.
- **Evidence.** 1120 passed, 1 skipped (the documented NVIDIA pointer section), with the database. `ruff` is clean. New or rewritten tests:
  - `test_judge_openai.py` (19): request shape, field order, cache keys, reply errors, failed status, a pinned strict schema;
  - `test_openai_backend.py` (13): body round trip, API and auth errors, key and base-URL handling, reply parsing, `openai_cost`, price rows, `import openai` with sockets blocked;
  - `test_judge_replay.py`: replays the recorded luna and Voyage responses offline;
  - harness tests in `test_eval_run.py` for the base-URL refusal, the header and the run-wide usage.
  - The request-shape tests first failed on the missing module. To show they test something, each was rerun against a deliberately broken request (a `temperature`, `store=true`, sorted schema keys, no provider in the key), and each failed.
- **Review (`mattpocock-skills:code-review`).**
  - Standards findings fixed: a golden test and a comment for the private SDK import `openai.lib._pydantic`; "Messages API" docstrings in `retrieve/answer_model.py` and `retrieve/response_cache.py` now name both providers; the pricing protocol renamed to `OpenAIReportedUsage` so it no longer shares the dataclass's name; model mismatch logged at warning, as for Anthropic; `total_usage` written as a plain sum.
  - Standards findings not changed: `OpenAIResponsesModel` repeats the client lifecycle, timing and error wrapping of `AnthropicAnswerModel`. Sharing them means editing the answer model, which this ticket keeps untouched. Worth a small refactor later. The base-URL refusal sits in both `eval.run` and `from_env`, kept as defence in depth.
  - Spec findings fixed: a run-wide usage total; a `failed` or `cancelled` response now stops the run instead of counting against one metric; CLAUDE.md now says pyproject pins the SDK and config records its version.
- **Corrected ticket 07 cost.** The ticket 07 entry above says about $10 to $15 per dev run with Opus. The recorded Opus responses show about $0.10 per judge run on an answerable question, so about $0.30 per question over three runs and about $25 per graded dev run (docs/research/openai-luna-judge.md, section 1.4). With luna at the recorded usage, a dev run's judging comes to about $0.52 (82 answerable × 3 runs × $0.0021, plus the decline and unanswerable runs).

**Next session starts with**
1. `/implement .scratch/step-5/issues/10-judge-spot-check.md`. Ticket 08 (the baseline) waits for its result.

## 2026-10-05 — Step 5 ticket 10: judge spot check (spot check, not calibration)

- **What changed.**
  - `python -m eval.spotcheck` answers 10 dev questions, then judges them with `gpt-6-luna` at `medium` and `high` and with `gpt-6-sol` at `medium`, 3 runs each, and compares each luna effort with sol. The questions are 2 per answerable class plus 1 decline and 1 unanswerable, chosen by a salted hash of the question text.
  - The comparison (`eval/judging/spotcheck.py`) is a pure function. Citation support, decline correctness and not-found correctness are compared as yes/no verdicts, one per sentence for citation support, so each judge run now keeps its verdicts (`RunScores.verdicts`). Verdicts are paired by run number. Faithfulness and answer relevancy use the gap between each judge's per-question mean.
  - `open_judges` takes a judge config, `eval.run`'s environment check is shared as `env_problem`, and `agent_drafted_set()` picks the agent-drafted set by name.
- **Pass rule, fixed before the run.** Per metric: verdict agreement of at least 85%; a mean absolute gap of at most 0.10; at most 15% of items scored by one judge only; something compared. The amendment fixed the first two. The user added the 15% cap and the 3 runs during review, before the run (spec amendment, "Spot-check rules settled before the run").
- **Answers.** The response cache was empty, because the baseline has never run. The spot check therefore wrote 10 new Sonnet answers ($0.30) into the shared cache, and the baseline will replay them. The ticket's "no new answer calls" now reads as "no answer is paid for twice" (user decision).
- **Result** (commit 6ac5e1f; 10 agent-drafted dev questions; 3 runs; judged scores uncalibrated; results file `benchmarks/runs/2026-10-05-spotcheck-dev-6ac5e1f.json`, not committed):

  | metric | rule | luna medium | luna high |
  |---|---|---|---|
  | citation support | agreement ≥ 0.85 | 0.945 (183 verdicts) | 0.951 |
  | decline correct | agreement ≥ 0.85 | 1.000 (3) | 1.000 |
  | not-found correct | agreement ≥ 0.85 | 1.000 (3) | 1.000 |
  | faithfulness | gap ≤ 0.10 | 0.068 (8 questions) | 0.059 |
  | answer relevancy | gap ≤ 0.10 | 0.0996 | 0.107, fail |
  | verdict | | **pass** | fail |

  No metric had missing values. Luna `medium` passes, so it stays the judge, and the config's model and effort do not change.
- **Caveats.**
  - The relevancy gap passes by 0.0004, on 8 questions, so the result is narrow.
  - `high` failing where `medium` passes is noise at this sample size. It is not evidence that more reasoning makes luna worse.
  - Decline and not-found rest on one question each.
  - None of this is calibration. Every judged number stays "uncalibrated" until it is checked against hand scores (Cohen's kappa ≥ 0.6, RESEARCH-PLAN).
- **Cost.** $1.73 in all: answers $0.30; luna `medium` $0.073 and luna `high` $0.079 (150 calls each); sol $1.28 (150 calls). Sol would cost about $12 per graded dev run, which matches the amendment's $12.40 estimate. Reasoning tokens: luna `medium` 34,409 of 73,410 output tokens, `high` 61,099 of 99,802, sol 16,772 of 55,887.
- **Output cap (config change, user decision).** Luna `medium`'s largest reply was 2,877 output tokens (median 233; `high` 5,352). `JUDGE_MAX_OUTPUT_TOKENS` went from 25,000 to 10,000. The cap is part of the cache key, so the 150 cached luna `medium` replies from this check will not replay, and the baseline re-judges those 10 questions (about $0.07). The judge replay test pins the cap the fixtures were recorded with (`RECORDED_CAP = 25_000`).
- **Evidence.** 1154 passed, 1 skipped (the documented NVIDIA pointer section), with the database. `ruff` is clean. New tests:
  - `test_spotcheck.py` (24): the thresholds, exact 85% and just under, a gap of exactly 0.10 and just over, the 15% missing cap, per-verdict and run-paired comparison, refusals of mismatched questions, runs and verdict counts, and the question choice checked against the real dev records;
  - `test_spotcheck_cli.py` (8): answers once, every judge sees the same answers, the choice falls to `high`, then to sol, failures write no file;
  - two in `test_judge_scoring.py` for the kept verdicts.
  - Each failed before its code existed. Breaking the 85% and 0.10 boundaries on purpose made the boundary tests fail.
- **Review (`mattpocock-skills:code-review`).**
  - Standards: no hard violations. Fixed: the duplicated environment check, positional `judges[:-1]`, the `[0]` question-set index, the unpacked question-set fields in `_header`, a duplicated name format in a test fake, and threshold arithmetic now in exact fractions. Not changed: the candidates name `gpt-6-luna` literally rather than reading the judge config, because the spec fixes them and the config changes after the run. The spot check also takes an `open_judges` callable rather than a `JudgeSpec`, because `JudgeSpec.open` cannot take a judge config.
  - Spec: the cached-answer gap, single-verdict behaviour rows and unbounded missing values went to the user, who settled them as above. A test now covers answers that were not cached.

**Next session starts with**
1. `/implement .scratch/step-5/issues/08-baseline-run.md` with `gpt-6-luna` at `medium` and the 10,000 cap.

## 2026-10-05 — Step 5 ticket 08: the vector baseline (the "before")

- **What ran.** The user ran `python -m ingest.embed` (0 embedded, 2,144 current, $0), then `python -m eval.run --arm vector --split dev`, then the same command with `--uncached`. Both runs are at commit 96e8c69: Sonnet answers at effort `high`, the gpt-6-luna judge at `medium` with a 10,000-token output cap, 3 judge runs, bootstrap seed 20261005.
  - The baseline file `benchmarks/runs/2026-10-05-vector-dev-96e8c69.json` is committed (forced past the `benchmarks/runs/*` ignore rule, the only run in git).
  - The repeat file `2026-10-05-vector-dev-96e8c69-2.json` stays local.
- **Gate.** Filter-excluded-gold is 0 over the 93 dev questions, in the run's own filter report and in `test_the_filter_excludes_no_dev_gold_chunk`. Filter exact match is 0.81 (companies 0.99, periods 0.82).
- **The first attempt stopped on a rate limit (fixed, 96e8c69).** Eight concurrent judge jobs went over gpt-6-luna's 200,000 tokens per minute. One request used up the SDK's six 429 retries, and the run stopped with no results file. The user chose to lower `JUDGE_WORKERS` from 8 to 4. Two barrier tests pin it: 4 jobs can meet at once, 5 never can. The "never five" test failed with the count at 8. Concurrency is in no cache key, so the rerun replayed the 93 answers and 742 judge replies the first attempt had cached. The uncached repeat at 4 workers got no 429s.
- **Baseline** (commit 96e8c69; agent-drafted questions; judged scores uncalibrated; 95% bootstrap intervals in brackets):

  | class | n | recall@5 | recall@10 | wrong evidence | faithfulness | relevancy | citation support | dropped share |
  |---|---|---|---|---|---|---|---|---|
  | lookup | 27 | 0.89 | 1.00 | 0/27 | 0.99 | 0.87 | 0.94 | 0.05 |
  | local | 22 | 0.93 | 0.98 | 0/22 | 0.98 | 0.74 | 0.97 | 0.06 |
  | multi_hop | 23 | 0.62 | **0.72** [0.54, 0.87] | 3/23 | 0.94 | 0.79 | 0.97 | 0.14 |
  | global | 10 | 0.59 | 0.80 [0.63, 0.95] | 2/10 | 0.79 | 0.78 | 0.97 | 0.14 |
  | decline (directional) | 6 | – | – | – | all 6 declined, decline correct 1.00 | | | 0 |
  | unanswerable (directional) | 5 | – | – | – | all 5 not_found, not-found correct 1.00 | | | 0.32 |

  Structural citation validity is 1.00 in every class: every cited chunk ID was among the retrieved chunks. No answer was truncated or refused, and no judge run failed.
- **The "before".** Multi-hop recall@10 is **0.72** [0.54, 0.87] on 23 agent-drafted dev questions. The plain vector top 10 finds every gold chunk on lookups, but on multi-hop questions it misses about a quarter of the gold chunks. In 3 of 23 multi-hop questions a hard negative outranks a gold chunk or is cited. The interval is wide because n is 23.
- **Run-to-run change (uncached repeat minus baseline).**
  - Retrieval, wrong evidence, statuses and the filter report are identical. Retrieval is deterministic, and every answer took the same status.
  - Judged means (uncalibrated) move by at most 0.031 per class: global faithfulness 0.787 → 0.756, local relevancy 0.744 → 0.768, multi-hop faithfulness 0.944 → 0.957, multi-hop citation support 0.975 → 0.963. Every other move is under 0.015.
  - Dropped share moves by up to 0.032 (global 0.137 → 0.169, lookup 0.054 → 0.022), because Sonnet writes different sentences each time.
  - So a judged difference under about 0.03 between arms is within run-to-run noise for one run each. Retrieval differences are not subject to this noise.
- **Cost.** About $6.80 in all, inside the approved $5–10.
  - Baseline: about $3.25. Answers $2.84 nominal, of which the 10 spot-check answers ($0.30) replayed. Judge $0.70 for 1,509 calls, split across the failed attempt and the rerun with nothing paid twice.
  - Repeat: $3.52 (answers $2.83, judge $0.69).
  - Reasoning tokens: 278,169 of 666,983 judge output tokens.
  - Per query: about $0.03 for the answer and $0.007 for three judge runs.
- **Evidence.** 1156 passed, 1 skipped (the documented NVIDIA pointer section), with the database. `ruff` is clean.
- **Review (`mattpocock-skills:code-review`, on the worker change).**
  - Standards: the first concurrency test asserted a timing-dependent peak. It was replaced by the two barrier tests. The comment now keeps the 429-retry note and drops the dated incident, which lives here instead.
  - Spec: no findings. Concurrency changes no score, cache key or header field, and the spec asks for no worker count in the header.
- **Step 5 stop condition met.** `eval.run --arm vector` produces a scored results file, and the multi-hop "before" is written down.

**Next session starts with**
1. Step 6 (CI quality gate): `/grill-with-docs` on the ~40-question CI subset, the regression thresholds against this baseline, and Phoenix tracing.
2. Open leads carried over: INTC FY2025 10-K headcount and capex table missing from chunks; NVDA Q3 FY2026 table header duplicated; judge calibration against hand scores before any judged number loses "uncalibrated".


## 2026-10-06 — Step 6 ticket 01: retrieval-only method on the vector arm

- **What changed.**
  - `VectorArm.retrieve(question)` filters, embeds and searches with no answer model call. It returns a `Retrieval` (`retrieve/arm.py`) with the retrieved chunks and scores, the question filter, the named companies with no retrieved chunk, the embedding and search timings, and the embedding tokens and cost.
  - `run()` calls `retrieve()` and then writes the answer, so the gate (ticket 03) and `eval.run` share one search path.
  - `ArmResult` now extends `Retrieval` and adds `answer` and `sources`. Its attributes are the same as before, so `eval.run`, the results file and the judges are untouched.
  - The arm accepts `answer_model=None`. `retrieve()` works without it, and `run()` raises `ArmError`. `open_vector_arm` and `VECTOR.required_env` are unchanged, so `eval.run` still needs `ANTHROPIC_API_KEY`.
  - Failures in `retrieve()` (Voyage, cache, database, cache file write) are raised as `ArmError` with the same message format as `run()`.
- **Tests (test-first, 8 new, all shown failing first).** `retrieve()` matches `run()`'s chunks, scores, filter, coverage, timings and cost; it sends no answer model request; it works with no answer model while `run()` refuses; k = 1 returns one chunk; embedding, cache and database errors become `ArmError`. The 19 existing vector arm tests are unchanged.
- **Evidence.** 831 passed and 14 skipped with the database (worktree at d870811 plus this change). The skips are the documented NVIDIA pointer section and the tests that need `data/raw` or `data/parsed`, which a worktree does not have. No database test skipped. `ruff` is clean. No eval slice was run: retrieval output is identical by test, and the gate that scores it comes in ticket 03.
- **Review (`mattpocock-skills:code-review`).**
  - Standards: `Retrieval` duplicated eight `ArmResult` fields, and `run()` copied them one by one. Fixed by making `ArmResult` extend `Retrieval`. The two exception tuples are now named constants. One test was renamed to say what it checks.
  - Spec: no missing requirements. `run()` wraps the same exception types as before, now split by phase; neither phase calls code that raises the types it no longer catches.
  - Left for ticket 03: with no answer model, `arm.config` still names the answer model, effort and prompt. If the gate records `ArmConfig`, it must leave those out. The snapshot query embedder should raise `CacheError` or `ArmError` for a missing vector, so that `retrieve()` wraps it.

**Next session starts with**
1. Ticket 03 (quality gate command) once ticket 02's snapshot has landed, carrying the two notes above.

## 2026-10-06 — Step 6 ticket 02: corpus snapshot and snapshot query embedder

- **What it is.** `tests/fixtures/corpus_snapshot.jsonl.gz` holds the local database's 24 `filings` rows (with parsed text), 2,144 `chunks` rows and 2,144 `voyage-4-large` `chunk_embeddings` rows, plus the query vectors of the 82 answerable `dev` agent-drafted questions. The file is **12.4 MB** (12,376,910 bytes, SHA-256 `f4e06d4d…`), a little above the 10–12 MB estimate. It stores no question text. Each query vector is keyed by model and the SHA-256 of the question text, as in the query cache.
- **Format.** The first line is a header with the format number, the model, the row counts and the SHA-256 of the lines after it. Rows follow in primary key order, with sorted keys and a zero gzip timestamp. Two rebuilds from the same data gave the same bytes. Vectors go in as pgvector's text form, so they round-trip exactly.
- **Code.** `eval/snapshot.py` builds, reads and loads the file. `SnapshotQueryEmbedder` serves the stored vectors through the `QueryEmbedder` interface and raises `SnapshotError` for any other question or model. It never calls Voyage. `ingest/store.py` gains `export_rows`, `import_rows` and `require_current_embeddings`. `python -m scripts.build_fixtures` writes the snapshot when `DATABASE_URL` is set and skips it otherwise. The script now has to run with `-m`, because the old `python scripts/build_fixtures.py` form cannot import `ingest`.
- **Loading.** `load_snapshot` checks gzip's CRC and the body hash, applies the schema, and inserts every row in one transaction. The schema's CHECKs run on each row. It refuses tables that already hold filings, and after the insert it refuses any chunk without a current vector. Filings and embeddings go in through new INSERTs, not `load_filing` or `save_embedding`. `load_filing` needs a `ParsedFiling` and runs the chunker, and `save_embedding` commits per row and stamps a new time.
- **What "identical rows" means.** The round-trip test compares every column except `loaded_at` and `embedded_at`. The snapshot leaves those timestamps out, since they would make each rebuild differ.
- **Fixture rebuild change.** `build_fixture` now leaves an HTML fixture alone when its content already matches `data/raw`. The committed fixtures carry a filename and timestamp in their gzip headers, so each rebuild used to rewrite all six with the same content.
- **Evidence.** `tests/test_snapshot.py` has 24 tests: byte-identical rebuilds, round trip between two throwaway schemas, a changed byte, an edited body, a failed `text_sha256` CHECK and a failed `chunk_id` CHECK, a stale vector, the embedder, the gated question set, no secrets, the committed file loading into an empty schema, and the committed file matching the local database and query cache (skips in CI). Full suite with the database, after rebasing on ticket 01: 1188 passed, 1 skipped (the documented NVIDIA pointer section). `ruff` is clean.
- **Review (`mattpocock-skills:code-review`).**
  - Standards: no hard violations, and the SQL is parameterized or built from column constants. All findings are fixed. Columns are now excluded by name, not by position. The embedding INSERT shares its prefix with the upsert, and the gap check is one function. The throwaway schema helper moved into `conftest.py`. Duplicate query vectors and JSON booleans are refused, and a damaged HTML fixture is rewritten instead of crashing the script.
  - Spec: `load_snapshot` now applies the schema itself. The fixture script builds the HTML and the filing list again without a database. The new INSERTs and the timestamp exception are recorded above.
- **For ticket 03.** `load_snapshot` returns a `Snapshot` with `sha256` and `query_vectors`. As ticket 01's notes asked, a missing vector raises `MissingQueryVector`, a subclass of both `SnapshotError` and `CacheError`, so `VectorArm.retrieve()` reports it as an `ArmError`. The gate should treat a `SnapshotError` from `load_snapshot` as "could not run".

**Next session starts with**
1. Step 6 ticket 03 (the `eval.gate` command). Tickets 01 and 02 are both in; read ticket 01's notes above as well.

## 2026-10-06 — Step 6 ticket 03: quality gate command and first gate baseline

- **What it does.** `python -m eval.gate` loads the committed snapshot into a new throwaway schema on `DATABASE_URL` and drops the schema afterwards, so it never reads or changes the tables already there. It runs `VectorArm.retrieve` with no answer model and the snapshot's query vectors on the 82 answerable `dev` agent-drafted questions. It then scores recall@5, recall@10 and the retrieval wrong-evidence rate (hard negatives ranked above a gold chunk, no citations) for lookup, local, multi_hop, global and pooled, and compares them with `benchmarks/gate_baseline.json`. Exit codes: 0 pass, 2 bad input, 3 could not run, 4 scores dropped. It needs no API key and makes no API call. A test removes every key, makes Voyage, Anthropic and OpenAI client construction raise, records every environment variable read, and runs the gate with the network blocked. No key is read.
- **Code.** `eval/gate_scores.py` holds the scores, the comparison and the question set hash. `eval/gate_baseline.py` holds the baseline file: a Pydantic model, atomic indented writes, input mismatches, and the change list. `eval/gate.py` is the CLI. `eval/snapshot.py` gains `gated_records`, which the gate and the snapshot both use. `write_json_atomic` gains an `indent` argument, so a baseline update reads as a plain diff.
- **Comparison.** Values are rounded to 9 decimal places before comparing; one question's worth is 1/82 at its smallest. The baseline stores full floats. Equal passes. Each drop prints as `DROPPED <class> <metric>: baseline …, current …, delta …`. The per-class table is labelled "agent-drafted questions" and is appended to `GITHUB_STEP_SUMMARY` only when that is set.
- **Baseline inputs.** The file records the commit, snapshot SHA-256, question set hash, embedding model, k, and n per class. A mismatch in any of the last four is exit 2, checked before the database is opened. The question set hash covers the 82 gated records, not the whole eval file, so an edit to a decline or `test` record does not force a baseline rewrite. `--update-baseline` refuses to replace an invalid old file. It prints each moved number and flags a lowering with "LOWERED" and the invariant 5 reminder.
- **First gate baseline** (agent-drafted questions, dev, k=10, voyage-4-large, snapshot `f4e06d4d…`, code commit 2de8eb7):

  | class | n | recall@5 | recall@10 | wrong evidence |
  |---|---|---|---|---|
  | lookup | 27 | 0.889 | 1.000 | 0.000 |
  | local | 22 | 0.932 | 0.977 | 0.000 |
  | multi_hop | 23 | 0.616 | 0.717 | 0.130 |
  | global | 10 | 0.592 | 0.800 | 0.200 |
  | pooled | 82 | 0.788 | 0.890 | 0.061 |

  `tests/test_gate_baseline_run.py` checks it against `2026-10-05-vector-dev-96e8c69.json`. Recall matches the run's cells exactly, as float equality, per class and pooled. The wrong-evidence rate matches the run's per-question hits whose `above_gold` is true. On this run the two rates happen to be equal in every class (multi_hop 3/23, global 2/10): no answer cited a hard negative that was not already ranked above gold. The first write recorded `f4d49bb+dirty` because the code was not yet committed. It was rewritten from a clean tree, and only the commit line changed. `python -m eval.gate` then passed locally.
- **k = 1.** The gate records k in the baseline, and the spec makes a k mismatch bad input. A change to `TOP_K = 1` therefore fails with exit 2 ("k is 1, but the gate baseline was measured with 10"), not exit 4. The test also scores k = 1 and shows pooled recall@5 and recall@10 drop against the k = 10 baseline. The ticket 05 stop-condition PR will show a red check for an input mismatch, not for a measured drop. Its PR text should say so.
- **Tests (test-first, each shown failing first).** 24 offline tests in `tests/test_gate_scores.py` cover scoring, comparison, rounding and the baseline file. 21 database tests in `tests/test_gate.py` cover:
  - a pass against its own baseline and against the committed baseline;
  - recall raised by one question's worth (multi_hop recall@10), and wrong evidence lowered by one question's worth (global);
  - k = 1;
  - each hash, model and k mismatch;
  - identical numbers on two runs and on two separate snapshot loads;
  - `--update-baseline` followed by a pass;
  - the summary file;
  - a missing, invalid or class-mismatched baseline, a damaged snapshot, an unset `DATABASE_URL`, and no `--split` option.

  11 tests in `tests/test_gate_baseline_run.py` compare the first baseline with the committed run. The gate tests take about 2.5 minutes, because each run loads the snapshot (about 6 s) and the vector arm's start-up vector check takes about 4 s.
- **Evidence.** `ruff` is clean. The full suite with the database (worktree at bb4d12d): 910 passed, 15 skipped. The skips are the documented NVIDIA pointer section and the tests that need `data/raw`, `data/parsed` or the local query cache, which a worktree does not have. This machine ran under heavy load (load average above 30), so the timings above are high.
- **Review (`mattpocock-skills:code-review`).**
  - Standards: two hard findings, both fixed. `--update-baseline` treated an invalid old file as missing, which would hide a lowering; it now stops with exit 2 before scoring. A failed write to `GITHUB_STEP_SUMMARY` raised a traceback; it now exits 3. Judgement calls, also fixed: the worse-direction rule existed twice and only one copy rounded, so it is now one shared `is_worse`. The display precision is one constant, `_Stop` was renamed `_GateExit`, and schema cleanup is skipped on a broken connection.
  - Spec: no missing requirement in the code. `compare()` raising on a baseline with other classes is now exit 2. The k = 1 reading, the gate loading the snapshot itself, and the scope of the question set hash are recorded above for the user to confirm.

**Next session starts with**
1. Ticket 04 (the gate in CI). The gate loads the snapshot itself, so the CI job needs `DATABASE_URL`, the pgvector extension, `TIKTOKEN_CACHE_DIR` and `python -m eval.gate`, with no separate load step.

---

## 2026-10-06 — Step 6 ticket 04: quality gate in CI

PR: https://github.com/Rohanvasudev1/fillingintel/pull/3 (branch `step-6/04-quality-gate-in-ci`).

- **Workflow.** `.github/workflows/ci.yml` has a new `quality-gate` job beside `lint-and-test`, on the same `pgvector/pgvector:0.8.6-pg16` service. It creates the vector extension and runs `uv run python -m eval.gate`, which loads the snapshot itself. The job's env holds only `DATABASE_URL`, plus `TIKTOKEN_CACHE_DIR` and `RAGAS_DO_NOT_TRACK` in case a later import needs them. No API keys. Triggers are `pull_request` and `push` to `main`, with no `paths` filters. The PR's first push started each check once.
- **Offline.** The gate makes no network calls because it reads its query vectors from the snapshot and builds no answer model. pytest-socket does not cover it, since it runs outside pytest, so this is true by design rather than enforced. `uv sync` downloads packages, as in `lint-and-test`.
- **Evidence (b57a504, clean tree).**
  - Local: `python -m eval.gate` passed (exit 0), with all 15 gated numbers equal to the baseline. `ruff` is clean. The full suite with the database gave 910 passed, 15 skipped. The env file was the main checkout's `.env`, because the worktree has none. These were rerun after a first round that ran before the final commit.
  - CI on the PR (run 37452634695): `lint-and-test` passed in 4m33s and `quality-gate` in 48s. The gate's table in the log matches the local run to six decimals (pooled recall@10 0.890244, n=82, agent-drafted questions). Linux and macOS pgvector give the same numbers.
  - The job summary could not be read from outside GitHub's UI (no API for step summaries). The gate writes its table to `GITHUB_STEP_SUMMARY`, and `tests/test_gate.py` covers that write. The user confirmed by eye that the table renders on the run page.
- **Review (`mattpocock-skills:code-review`).** No hard Standards violations and no blocking Spec findings. Fixed: the gate job now uses the same `uv sync --group dev` as `lint-and-test` and sets the two offline env vars. Kept as is:
  - The duplicated service and setup steps, which keep the two checks separate and clearly named for the ticket 05 ruleset.
  - Exits 2 and 3, which write only to the log and not to the summary.
  - On `pull_request`, checkout uses the merge commit, so the commit the gate reports is the merge SHA.
- **Raised, not done.** The CI annotations warn that `actions/checkout@v4` and `astral-sh/setup-uv@v3` run on Node.js 20, which is deprecated, and that `ubuntu-latest` moves to Ubuntu 26 from 2026-10-19. Adding `permissions: contents: read` and `timeout-minutes` was proposed to the user. All three are beyond the spec.

**Next session starts with**
1. After the user merges PR #3, check that the `push` run on `main` is green and mark ticket 04 done.
2. Ticket 05: the `main` ruleset (only after the user approves the settings change) and the k = 1 stop-condition PR. That PR fails with exit 2 (k mismatch), not exit 4.

---

## 2026-10-06 — Step 6 ticket 05: protect `main` and the stop condition

Ticket 04 is also closed: PR #3 merged, and the `push` run on `main` (37455002708) passed both checks.

- **Ruleset.** The user approved the exact `gh api` call in chat, and then I created ruleset 24576386, "main: CI and quality gate must pass" (https://github.com/Rohanvasudev1/fillingintel/rules/24576386). It targets `~DEFAULT_BRANCH`, is `active`, and requires `lint-and-test` and `quality-gate` from GitHub Actions (integration 15368). `strict_required_status_checks_policy` is false. It has no bypass actors. Reading it back with `gh api .../rulesets/24576386` and `.../rules/branches/main` showed both checks, `bypass_actors: []` and `current_user_can_bypass: never`. The research had recommended an admin bypass for PRs only. The ticket has none, so the owner is bound too.
- **Direct push rejected.** I made an empty commit on a detached `origin/main` and pushed it to `main`. GitHub refused it with `GH013: Repository rule violations found for refs/heads/main ... 2 of 2 required status checks are expected` and `push declined due to repository rule violations`. `main` stayed at cf56ac9. Nothing was forced. The ruleset has no "require a pull request" rule, so a commit that already passed both checks on a branch could still be pushed straight to `main`.
- **Stop condition (k = 1).** PR: https://github.com/Rohanvasudev1/fillingintel/pull/4, closed without merging, branch deleted. With the user's agreement, the PR changed the vector arm's search to ask for 1 chunk (`retrieve/vector.py:124`) and left `TOP_K` at 10. A plain `TOP_K = 1` would have failed as an input mismatch (exit 2) without naming any scores. CI run 37456438739:
  - `quality-gate` failed with exit 4. It named 10 dropped numbers, every recall@5 and recall@10, per class and pooled. Pooled recall@10 fell from 0.890244 to 0.417683, and multi_hop recall@10 from 0.717391 to 0.166667 (agent-drafted questions).
  - The wrong-evidence rate fell in every class (pooled 0.060976 to 0.012195). With one chunk, fewer hard negatives rank above gold. The gate only fails a rise, so these rows read "ok". Recall is what catches this kind of break.
  - `lint-and-test` also failed: 9 tests in `tests/test_vector_arm.py` and `tests/test_gate.py`.
  - `gh pr view 4` reported `mergeStateStatus: BLOCKED`.
- **Workflow from now on.** Every change, this entry included, reaches `main` through a PR with both checks green.
- **v1.** RUNBOOK steps 1–6 are v1. v1 is complete once this ticket and the tracing tickets (06, 07, 08) are done. Only the tracing tickets remain.
- **Evidence.** No repository code changed in this ticket, so no new tests and no code review. The only commit is this entry and the ticket statuses.

**Next session starts with**
1. Ticket 06 (Phoenix service in docker-compose.yml), on its own branch and PR.

## 2026-10-06 — Step 6 ticket 06: Phoenix in Docker Compose, services on localhost

- **Compose.** A `phoenix` service runs `arizephoenix/phoenix:20.19.0` (digest `sha256:d240d8d4…`, as in the research note). It sets `PHOENIX_WORKING_DIR=/mnt/data` on the named volume `phoenix_data`, with `PHOENIX_TELEMETRY_ENABLED=false` and `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false`. Ports 6006 and 4317 are published on 127.0.0.1. Postgres (5432) and Neo4j (7474, 7687) now publish on 127.0.0.1 too, so `docker ps` shows no `0.0.0.0` binding.
- **Healthcheck.** The image is distroless, with entrypoint `/usr/bin/python3.13`. `python` and `python3` both exist in it. The check runs `python3 -c "urllib.request.urlopen('http://127.0.0.1:6006/healthz', timeout=3)"`. Run by hand in the container it exits 0, and it exits 1 against a port nothing listens on. Compose reports the service `healthy`.
- **Persistence.** I sent one CHAIN span over OTLP HTTP to `/v1/traces` from a throwaway `uv run --no-project` environment, under project `ticket06-check` (trace `529ff5b2b2d831276e719c392727a93b`). After `docker compose down` (0 containers left) and `up -d`, `GET /v1/projects/ticket06-check/spans` still returned it. The project is still in Phoenix and can be deleted from the UI.
- **No third-party requests.** In the browser pane I loaded the UI, the project page and the trace view. The network log and `performance.getEntriesByType('resource')` showed requests to `localhost:6006` only. The served `index.html` has no Scarf, FullStory or Google Fonts reference.
- **Text-capture setting.** `.env.example` names it `FILINGINTEL_TRACE_TEXT`, commented out. The spec left the name open. Ticket 07 has a note to use it, and to append `/v1/traces` to `PHOENIX_COLLECTOR_ENDPOINT`.
- **Compose project name.** The worktree's directory name differs from the repo's, so I ran compose with `-p fillingintel` to reuse the existing `fillingintel_postgres_data` and `fillingintel_neo4j_data` volumes. Until this PR merges, `docker compose up -d` from the main checkout recreates Postgres and Neo4j on all interfaces and warns that `phoenix` is an orphan container.
- **Evidence.** `uv run ruff check .` is clean. `uv run --env-file .env pytest`: 910 passed, 15 skipped. `tests/test_store.py`: 31 passed against the rebound Postgres. No Python code changed, so there are no new tests and no eval slice.
- **Review** (`mattpocock-skills:code-review`). Spec: nothing missing. Standards: no hard violations. Fixed: the healthcheck uses 127.0.0.1 instead of `localhost`, the CLAUDE.md Commands line is split in two, and the notes went into ticket 07. Kept: the 4317 mapping, flagged as Speculative Generality, because the ticket and spec ask for it.

**Next session starts with**
1. Merge this ticket's PR, then ticket 07 (tracing for retrieval and answers).

## 2026-10-06 — Step 6 ticket 07: tracing for retrieval and answers

- **What.** `retrieve/tracing.py` builds a `TracerProvider` only when `PHOENIX_COLLECTOR_ENDPOINT` is set: a `BatchSpanProcessor` and an OTLP HTTP exporter to `{endpoint}/v1/traces` with a 2 s timeout, sampler pinned to always-on, resource `openinference.project.name=filingintel`. It never calls `trace.set_tracer_provider`. `eval.run` opens one CHAIN root span per question (`answer_question`: question text, metadata with question ID, set, arm, split and class, and the answer status as output) and shuts the provider down in a `finally`. The vector arm takes an optional `SpanRecorder` (tracer plus text-capture setting) and records `question_filter` (CHAIN), `query_embedding` (EMBEDDING), `vector_search` (RETRIEVER, chunk IDs and scores) and `answer_generation` (LLM). With no recorder it records nothing. Attribute names come from `openinference-semantic-conventions`.
- **Tokens and cost.** Claude's `llm.token_count.prompt` is uncached input plus cache reads plus cache writes, with the cache details in their own attributes. A response-cache replay carries no `llm.token_count.*`. `filingintel.cost_usd` holds the run file's cost on the LLM and EMBEDDING spans, and `filingintel.cache_hit` says whether the call was replayed. A cached query embedding still shows the Voyage cost the run file records; it was not billed.
- **Text.** Question text is always on spans. Prompt and answer text go on the LLM span only with `FILINGINTEL_TRACE_TEXT=true`; the prompt recorded is the request actually sent, not a re-render. `eval.run` exits 2 if the endpoint is not an http(s) base URL (credentials, a query or a fragment are refused) or if the text setting is anything but true or false.
- **Dependencies.** `opentelemetry-sdk==1.45.0`, `opentelemetry-exporter-otlp-proto-http==1.45.0`, `openinference-semantic-conventions==0.1.41`. `uv.lock` adds 11 packages (the research note's 10 plus the conventions package) and changes no existing version.
- **Tests.** `tests/test_tracing.py` (offline) and `tests/test_tracing_run.py` (database), written first and seen failing. They cover span names, kinds, parent links and attributes for one question through `eval.run` with the real vector arm, the fake embedder and the scripted answer model; no token counts on a replay; text only with the setting on; no span attribute or event holding an API key, the database URL or a header name; a traced and an untraced run writing the same file apart from `created_at`; no spans without a recorder; exit 2 on a bad setting; and shutdown against a closed port taking under 4 s. An autouse fixture in `tests/conftest.py` removes `PHOENIX_COLLECTOR_ENDPOINT`, `FILINGINTEL_TRACE_TEXT` and `OTEL_EXPORTER_OTLP_*`. The vector arm fakes moved to `tests/vector_fakes.py` so both test files share them.
- **Demo.** With Phoenix up, a cached traced `eval.run --arm vector` over all 93 dev questions ran with fake API keys in the shell, so any cache miss would have failed instead of billing. It finished in 13.7 s. Phoenix's project `filingintel` holds 93 traces of 5 spans each (465 spans). The UI shows each tree (root, filter, embedding, search, generation) with the question as input and the status as output, and reports a total cost of $0, because every LLM span was a replay. Through the REST API the RETRIEVER span lists `retrieval.documents.{i}.document.id` and `.score`, and the LLM span has `filingintel.cache_hit=true` and no token counts.
- **Tracing changes nothing measured.** The traced run's file equals an untraced cached run's apart from timing fields (316 timing leaves differ, 0 others). Against the committed baseline (`2026-10-05-vector-dev-96e8c69.json`), retrieval, answers and costs are identical. Answer relevancy's lookup mean moves by 9.4e-6, and `judged.replayed` rises because the baseline made fresh calls. Both come from the cache replay, since the untraced replay gives the same numbers.
- **Phoenix stopped.** With the container stopped and the endpoint set, the same run finished in 13.5 s, as fast as the untraced run. The exporter logged one warning and one error and raised nothing.
- **Evidence.** `uv run ruff check .` clean. `uv run --env-file .env pytest`: 1282 passed, 1 skipped (the worktree had `data/` linked, so the local-corpus tests ran; without it the count was 948 passed, 13 skipped). `python -m eval.gate`: PASS, every gated number unchanged (pooled recall@10 0.890244, n=82, agent-drafted questions).
- **Review** (`mattpocock-skills:code-review`). Fixed: the autouse fixture also clears `FILINGINTEL_TRACE_TEXT`; endpoints with a query or fragment are refused, so nothing secret can reach the "tracing to" log line; the Voyage input type is a shared constant (`ingest.voyage.QUERY_INPUT`); the LLM span records the request as sent instead of re-rendering the prompt. Kept: `None` as "no recorder", because the ticket asks for an optional tracer; `_write_answer` takes the answer model as a parameter so the type checker sees it is not `None`; question text on the filter and search spans, as the research note's span table lays out; `_execute`'s keyword parameters, which exist only to wrap the run in `try/finally`. Noted for ticket 08: the root span ends before judging starts, so judge spans need the question's span context kept and passed as parent.

- **PR.** Commit 91326ba on branch `claude/tracing-retrieval-answers-09eea4`, opened as https://github.com/Rohanvasudev1/fillingintel/pull/7. Auto-fix is on for it (user's request, 2026-10-07): a CI failure, merge conflict or review comment wakes the session that opened it, which fixes, verifies and pushes to the same branch. The user still merges. (Restored in the Step 6 wrap-up: PR #8, which added this line, was closed unmerged.)

**Next session starts with**
1. Merge this ticket's PR, then ticket 08 (judge spans), starting from the note at the top of its ticket.

## 2026-10-07 — Step 6 ticket 08: judge spans

- **What.** Each question's judge calls now sit in its trace. `RagasJudges` opens one EVALUATOR span per metric per judge run, named after the metric, with `filingintel.judge.metric`, `filingintel.judge.run` and the score as JSON in `output.value`. A reply the judge cannot score marks the span ERROR and records the reason in `filingintel.judge.error`. `OpenAIJudge` wraps each call in an LLM span, `judge_call`, which records the model, provider `openai`, effort and output cap, the cache hit and the run file's cost. Attribute helpers live in `eval/judging/tracing.py`.
- **Parenting.** The `answer_question` root span ends before judging starts. `_run_arm` therefore returns each root's span context next to its outcome, and `judge_all(..., parents=...)` runs every job through `contextvars.copy_context().run` with that context attached. The judge spans start under the root after the root has ended, and Phoenix shows them in the same trace. The context is kept out of `Outcome`, because `Outcome` is results-file data.
- **Tokens.** A fresh judge call carries OpenAI's reported counts: `input_tokens` as the prompt count (it already includes cached and written tokens), cached tokens as `cache_read`, written tokens as `cache_write`, `output_tokens` as completion, reasoning tokens as `completion_details.reasoning`, and their sum as the total. A replay carries no `llm.token_count.*`. As for answers, `filingintel.cost_usd` keeps the recorded cost.
- **Interfaces.** `RagasJudges`, `OpenAIJudge` and `open_judges` take an optional `SpanRecorder`. With none they record nothing and score exactly as before (tested). `JudgeSpec.open` now takes `(cache, spans)`, like `ArmSpec.open`. The judge fakes in `tests/test_eval_run.py` and `tests/test_tracing_run.py` gained a `spans` parameter. The runner, replay and scoring tests are unchanged.
- **Tests.** `tests/test_judge_tracing.py` was written first and seen failing. It runs the real Ragas judges on the scripted backend through `judge_all`'s thread pool and checks:
  - each `judge_call` sits under an EVALUATOR span under its own question's root, and the backend ran off the main thread;
  - EVALUATOR attributes match the returned scores;
  - fresh calls carry the reported token counts and replays carry none;
  - with no recorder there are no spans and the scores are the same;
  - with no parents, each judge span starts its own trace;
  - a parent count that does not match the items raises `ValueError`;
  - an unscorable reply marks the metric span ERROR. This test was added after review and passed at once, so I checked it fails with the status line removed.

  `tests/test_tracing_run.py` adds one database test: through `eval.run`, judge spans sit under `answer_question` and start after it ends.
- **Demo.** With Phoenix up and `PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006`, a cached `eval.run --arm vector` scored all 93 dev questions with no paid calls. The run file shows all 93 answers and all 93 query embeddings replayed, and 1,509 of 1,509 judge calls replayed. This run used the real keys, not fake ones as in ticket 07. Nothing was billed, but a cache miss would have been. Phoenix's REST API returned 2,745 spans from the run:
  - 93 roots, each with 3 to 9 EVALUATOR spans (771 in all);
  - every EVALUATOR span's parent is its question's `answer_question`, in the same trace;
  - 1,509 `judge_call` spans, each under an EVALUATOR span, all `cache_hit=true` with no token counts.
- **Scores unchanged.** The traced run's file matches an untraced cached run's apart from timing fields (316 timing leaves differ, 0 others), as in ticket 07. Against the committed baseline (`2026-10-05-vector-dev-96e8c69.json`), `eval.compare` shows retrieval, answers and costs identical. Answer relevancy's lookup mean moves by 9.4e-6 and `judged.replayed` rises. These are the same cache-replay differences ticket 07 recorded.
- **Evidence.** `uv run ruff check .` clean. `uv run --env-file .env pytest`: 1291 passed, 1 skipped (with `data/` linked, so the local-corpus tests ran). `python -m eval.gate`: PASS, every gated number unchanged (pooled recall@10 0.890244, n=82, agent-drafted questions).
- **Review** (`mattpocock-skills:code-review`).
  - Spec: nothing missing.
  - Standards: no hard violations. Fixed: an unscorable reply now marks its EVALUATOR span ERROR; a test import moved to the top of the file.
  - Kept: the parent contexts as a list next to the outcomes, with a length check, rather than a field on `Outcome`, which would put OpenTelemetry objects in results data. Also kept: `_open_configured_judges`, because `open_judges`'s positional `config` is used by `eval.spotcheck`; separate judge and answer token helpers, because the two providers count prompt tokens differently; and the test's import of `_replies` from the scoring tests, because moving it would edit a scoring test the ticket says stays unchanged.
- **Decision for the user.** Judge prompt text never goes on spans, even with `FILINGINTEL_TRACE_TEXT=true`. Recommendation: keep it off. Judge prompts repeat the chunk text and answer already on the answer span, and they are large.

**Next session starts with**
1. Merge this ticket's PR. That finishes Step 6's tickets; next is the Step 6 wrap-up against the RUNBOOK.

## 2026-10-07 — Step 6 wrap-up ✅ (Step 6 closed; v1 complete)

- **Tickets.** All eight merged through their own PRs: 01 (retrieval-only arm), 02 #1, 03 #2, 04 #3, 05 #5, 06 #6, 07 #7, 08 #9. PR #4 was the k = 1 stop-condition PR, closed unmerged. PR #8 was closed unmerged; its one line about PR #7 is restored in the ticket 07 entry above. Every acceptance box in the eight tickets is ticked.
- **Spec check.** I read the 58 user stories in `.scratch/step-6/spec.md` against `main` at 335f731 and the ticket entries. All are met:
  - Gate (stories 1–24): offline, 82 questions, zero tolerance, named drops, job summary, baseline file, first baseline matched to the committed run (`tests/test_gate_baseline_run.py`).
  - Snapshot (25–32): one gzipped file, hash-checked on load, matched against the local database by a test that skips in CI.
  - PRs and protection (33–38): one PR per ticket, ruleset 24576386, `ci.yml` triggers on `pull_request` and pushes to `main` with no paths filters.
  - Tracing (39–54): tickets 07 and 08.
  - Compose (55–58): ticket 06.
- **Stop condition.** Met in ticket 05 (PR #4, CI run 37456438739). The RUNBOOK now has a "Step 6 — outcome" section, as Step 3 has.
- **CI on `main`.** The push run for the PR #9 merge (37612322365, commit 335f731) passed both `lint-and-test` and `quality-gate`.
- **Docs.** RUNBOOK outcome section. OPEN-DECISIONS: the Steps 5–6 entry now says what Step 5 finished (pinned judges, 3 runs, bootstrap intervals) and what is open (judge calibration, paired comparisons between arms). Four items raised during the tickets went to the user; see Decisions below. Ticket statuses for 06–08 and the spec now name their PRs.
- **Cleanup (user's go-ahead, 2026-10-07).**
  - The k = 1 branch `step-6/05-stop-condition-k1` was already deleted on GitHub, as ticket 05 says. My local list of remote branches was stale; `git fetch --prune` fixed it.
  - Removed five merged worktrees under `.claude/worktrees/` and their local branches, plus four other merged local branches. Each worktree was checked first for uncommitted changes and commits not on `main`. The only such commit was the PR #7 line restored above. The ticket 07 demo's run file was copied to the main checkout's `benchmarks/runs/` before its worktree was removed.
  - Kept `retrieval-only-vector-arm-15a72c`: it holds its own `.env`, which invariant 7 stops me reading or comparing with the main one.
- **Decisions (user, 2026-10-07).** All four "Step 6 — to confirm" items as recommended. The first three are now in CLAUDE.md Decisions. The fourth: the user approved the exact `gh api -X PUT .../rulesets/24576386` call in chat, and I applied it. The ruleset now has two rules, the existing required checks (`lint-and-test`, `quality-gate`) and `pull_request` with 0 required approvals, still with no bypass actors. GitHub added `require_extra_approval_for_unattributed_changes: true` on its own; PR #10 still reported `mergeStateStatus: CLEAN` afterwards, so it does not block the user's PRs. An empty commit pushed straight to `main` from a fresh clone was refused with `GH013 ... Changes must be made through a pull request`, and `main` stayed at 335f731.
- **Evidence.** No code changed, so no new tests and no code review. `uv run ruff check .` is clean.

**Next session starts with**
1. Step 7 (ontology as code), starting with `/grill-with-docs`. Judge calibration stays open until before the final benchmark.

## 2026-10-07 — Step 7 ticket 01: ontology module and schema text

- **What.** A new `graph` package. `graph/ontology.py` holds the 14 node labels (4 structural, 10 extracted) and 20 edge types as frozen dataclasses in tuples, with read-only lookups by name. Each definition has its kind, keys, typed properties (`Prop.required` marks optional `stake` on `OWNS`), endpoints for edges, and a hand-written one-line description. `graph/schema_text.py` renders the labels, then the edge types, in the module's order, and exposes `SCHEMA_TEXT_SHA256`. `ONTOLOGY_VERSION = 1`, hash `9f2687c2…57fb37` (after the rename below). `pyproject.toml` adds `graph` to ruff's first-party list. No new dependency.
- **Types I chose (the spec names properties, not types).** `cik`, `fiscal_period`, `period` and `key` are STRING, `filed_date` (renamed below) is DATE, `MetricValue.value` is FLOAT, and `stake` is FLOAT, described in the schema text as the owned share from 0 to 1. A type change is an ontology edit, so it needs version 2 once anything records version 1.
- **TDD.** `tests/test_ontology.py` was written first. Against stub modules with empty definitions, 40 tests failed and 7 passed, because they loop over empty tuples. One failure was in the test itself: its parser of the CLAUDE.md endpoint table stopped at the first name in "SUPPLIES, CUSTOMER_OF, COMPETES_WITH". I fixed the regex, not the module. The endpoint test reads that CLAUDE.md line directly, so the module and the doc can't drift apart without a failure.
- **Code review** (`mattpocock-skills:code-review`, both axes). Spec: every acceptance item met. Standards: no hard violations. Fixed: type hints and class docstrings in `ontology.py`; the docstring no longer claims the module stops other files from defining types; clear failure messages when the CLAUDE.md line changes shape; clearer test helper names; a new test that labels, edge types and property names are plain identifiers, ahead of ticket 03 putting them into Cypher. Kept as-is: the parallel `_label_block` and `_edge_block`, and plain strings for endpoint labels (tests check each names a known label).
- **Evidence.** `uv run ruff check .` is clean. `uv run --env-file .env pytest`: 1005 passed, 15 skipped, of which 48 are in `tests/test_ontology.py`. Of the skips, 14 need `data/raw`, `data/parsed` or the local query cache, which this worktree lacks; the 15th is the documented pointer-section skip. No graph test needs that data.
- **Decisions (user, 2026-10-07), all as recommended.**
  1. Filing's date property is `filing_date`, as in Postgres `filings` and `FilingMeta`, so the Step 10 loader copies the column without renaming it. The spec said `filed_date`. The test was changed first and failed, then the module. The pinned hash failed as designed and was re-pinned at version 1 (`9f2687c2…57fb37`), since nothing had recorded the old hash. The spec line, CLAUDE.md Decisions and this entry say so.
  2. The types above stay. Ticket 03 gains a box: the batch check accepts an int for `MetricValue.value` and the write path stores it as a float.
  3. Ticket 03 gains a box to add `graph/` to `git_state()`'s +dirty list.

**Next session starts with**
1. Merge this ticket's PR. Next is ticket 02 (Neo4j test instance and constraints), which adds `neo4j==6.3.1` and the CI service.

## 2026-10-07 — Step 7 ticket 02: Neo4j test instance and constraints

- **What.** `neo4j==6.3.1` added (lock adds `neo4j` and `pytz` only). `graph/connection.py` builds the driver from `{prefix}_URI`, `_USER` and `_PASSWORD` with `telemetry_disabled=True` and names every missing setting; `DATABASE = "neo4j"` is passed on every query. `graph/constraints.py` derives 14 node uniqueness constraints from the ontology (`company_cik_unique`, `period_cik_fiscal_period_unique`, `organization_key_unique` and so on), creates them with `IF NOT EXISTS`, reads `SHOW CONSTRAINTS` back and raises `ConstraintMismatch` listing missing, different and extra constraints on any ontology label, edge type or `:GraphMeta`. Then it creates the single `:GraphMeta {ontology_version, schema_sha256}` in a write transaction, or raises `GraphMetaMismatch`.
- **One change from the spec's order.** The spec lists the `:GraphMeta` check as step 4, after the constraints. `apply_constraints` checks it first as well, so a graph from another ontology is refused before any constraint is created ("refuse without changing it", ticket 02), and again inside the MERGE transaction.
- **Test database.** `neo4j-test` in docker-compose (same image, bolt on 127.0.0.1:7688, volume `neo4j_test_data`, credentials from `NEO4J_TEST_USER` and `NEO4J_TEST_PASSWORD`); a Neo4j service container in CI's `lint-and-test` job, health-checked with a `cypher-shell` Bolt query; `quality-gate` unchanged. Graph tests use only `NEO4J_TEST_*` and skip without `NEO4J_TEST_URI`; a guard test fails in CI if it is unset. `tests/neo4j_fixtures.py` creates the `:FilingIntelTestMarker` node only in a database with no nodes and no constraints, fails the session on data without a marker, and before each test deletes every other node, every constraint and every non-lookup index.
- **TDD.** `tests/test_graph_constraints.py` was written first. Without `graph.connection` it failed at import; against a stub `apply_constraints` raising `NotImplementedError`, 15 failed and 3 passed (the CI guard, the settings test and the marker test). Then the module: all passed.
- **Code review** (`mattpocock-skills:code-review`, both axes). No hard violations; injection safety, database names and telemetry confirmed. Fixed: the `apply_constraints` docstring now says a mismatch can leave the ontology's own constraints created (never `:GraphMeta`); one shared expected-names set; constraints on `:GraphMeta` count as extra (new test); the concurrency comment is accurate; fixture import moved to the top of conftest; a `_marker_count` helper; the three-case claim test split in three with an `unclaimed` fixture. Not changed: `${NEO4J_TEST_PASSWORD:?}` in compose would make every `docker compose up` fail until `.env` has it, so the service keeps the existing `neo4j` idiom (it exits when unset; the other services start). CI's Neo4j password is a labelled CI-only test value, like the Postgres one. Ticket 04 gains a note: `validate_graph()` will see the test marker as an unknown label.
- **Evidence.** `uv run ruff check .` clean. Full suite with `NEO4J_TEST_*` set against the local `neo4j-test`: 1360 passed, 1 skipped (the documented pointer-section skip) after the review fixes, 21 of them in `tests/test_graph_constraints.py`. CI result is on the PR.
- **Needs from the user.** Add `NEO4J_TEST_URI=bolt://localhost:7688`, `NEO4J_TEST_USER=neo4j` and a `NEO4J_TEST_PASSWORD` (8+ characters) to `.env`, then `docker compose up -d neo4j-test`. The container I started for this session uses a generated password, so remove it first (`docker compose rm -sf neo4j-test` and `docker volume rm fillingintel_neo4j_test_data`).

**Next session starts with**
1. Merge this ticket's PR. Next is ticket 03 (guarded write path).

---

## Findings worth telling

Short versions of the stories from this build so far, for interviews and write-ups.

- **Test-first caught a real fiscal-calendar bug.** NVIDIA's fiscal year ends in late January, so a naive date calculation mislabels its filings by a year. A test written before the code caught it. The labels were then cross-checked against the filings' own XBRL tags.
- **Choosing a parser from evidence.** A one-day spike on real filings showed one popular library failing completely on Intel's filings. That would have silently removed a third of the corpus.
- **The citation guarantee nearly broke.** Only 3 of 29 section offsets from the library round-tripped. Instead of patching this with text search, offsets are now correct by construction.
- **"Present" is not "correct".** The first Step 2b result said all required sections were present on all 6 filings. Running 24 showed some were 69-character table-of-contents rows and one was the exhibit list. Acceptance checks need to test content, not existence.
- **Reviewing the agent's tests, not just its code.** Several agent-written tests would have passed while proving nothing: a loose rate-limit threshold, a circular round-trip test, and fixtures written by the same agent as the parser. Catching these is part of the job.
- **A schema's key order changed what the judge wrote.** Sorting a JSON schema's keys for a stable hash put the verdict ahead of its reason. The judge, which writes fields in schema order, then left every reason blank. Keeping the declared order brought the reasoning back. Small serialization choices can change what an LLM produces.
