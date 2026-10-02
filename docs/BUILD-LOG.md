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
- Tests: 415 passed, 1 skipped (the documented pointer case) before the final review fixes; coverage 97-98% on `ingest/corpus.py` and `ingest/parser.py`; ruff and pyright clean on production modules.

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

## Findings worth telling

Short versions of the stories from this build so far, for interviews and write-ups.

- **Test-first caught a real fiscal-calendar bug.** NVIDIA's fiscal year ends in late January, so a naive date calculation mislabels its filings by a year. A test written before the code caught it. The labels were then cross-checked against the filings' own XBRL tags.
- **Choosing a parser from evidence.** A one-day spike on real filings showed one popular library failing completely on Intel's filings. That would have silently removed a third of the corpus.
- **The citation guarantee nearly broke.** Only 3 of 29 section offsets from the library round-tripped. Instead of patching this with text search, offsets are now correct by construction.
- **"Present" is not "correct".** The first Step 2b result said all required sections were present on all 6 filings. Running 24 showed some were 69-character table-of-contents rows and one was the exhibit list. Acceptance checks need to test content, not existence.
- **Reviewing the agent's tests, not just its code.** Several agent-written tests would have passed while proving nothing: a loose rate-limit threshold, a circular round-trip test, and fixtures written by the same agent as the parser. Catching these is part of the job.