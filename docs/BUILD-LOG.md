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

## Findings worth telling

Short versions of the stories from this build so far, for interviews and write-ups.

- **Test-first caught a real fiscal-calendar bug.** NVIDIA's fiscal year ends in late January, so a naive date calculation mislabels its filings by a year. A test written before the code caught it. The labels were then cross-checked against the filings' own XBRL tags.
- **Choosing a parser from evidence.** A one-day spike on real filings showed one popular library failing completely on Intel's filings. That would have silently removed a third of the corpus.
- **The citation guarantee nearly broke.** Only 3 of 29 section offsets from the library round-tripped. Instead of patching this with text search, offsets are now correct by construction.
- **"Present" is not "correct".** The first Step 2b result said all required sections were present on all 6 filings. Running 24 showed some were 69-character table-of-contents rows and one was the exhibit list. Acceptance checks need to test content, not existence.
- **Reviewing the agent's tests, not just its code.** Several agent-written tests would have passed while proving nothing: a loose rate-limit threshold, a circular round-trip test, and fixtures written by the same agent as the parser. Catching these is part of the job.