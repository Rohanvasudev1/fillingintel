# Step 3 plan: chunking

Status: approved 2026-10-03. The user accepted all six recommendations.

Fixed point for /step-review: set per sub-step when it starts.
- 3a: e88cc45
- 3b: de340b7
- 3c: fdc87fc

## Spec (docs/RUNBOOK.md, Step 3)
- Chunks follow sections and never cross an Item boundary.
- About 800 tokens per chunk with about 100 tokens of overlap. Tables stay whole, even if a chunk goes over size.
- Every chunk stores `chunk_id`, `cik`, `accession_no`, `form_type`, `fiscal_period`, `section`, `char_start` and `char_end`.
- Stop condition: a chunk table in Postgres, and `resolve(chunk_id)` returning the exact source text for a random sample of 20 chunks.

## Measurements behind the plan (cl100k tokens, 2026-10-03)
- NVDA 10-K is 76k tokens. Item 1A alone is 20.6k tokens.
- The parsed text already holds each table as a run of `|` lines. NVDA has 67 such blocks against 64 edgartools tables; INTC 10-Q has 118 against 121.
- Table sizes have a median of 13 to 182 tokens and a maximum of 1,825. 3 NVDA tables and 5 INTC tables are over 800 tokens.
- `data/parsed/` doesn't exist yet, although CLAUDE.md names it.

## 3a: finding tables
- Find tables by scanning the parsed text for consecutive `|` lines. Each table gets exact offsets and the section that contains it, with no label matching, so no table is indeterminate.
- The corpus report shows, per filing, the blocks found against the tables edgartools detected, and explains any gap.
- New module `ingest/tables.py` with a frozen `TableSpan` model.
- Acceptance: on all 24 filings, every block equals `text[start:end]`, and no block crosses a section boundary.

## 3b: chunker (pure Python)
- A section is split into blocks: paragraphs (split on blank lines) and whole tables. Blocks are packed into chunks of up to 800 tokens.
- A paragraph over 800 tokens is split at sentence ends.
- Overlap goes back up to 100 tokens, starting at a paragraph or sentence start, and never inside a table.
- Each span of a multi-span item is chunked on its own. The section label stays the same across its spans.
- The output is a `Chunk` model: the spec fields plus `ordinal` and `token_count`.
- Acceptance, on the 6 fixtures (unit tests) and on all 24 filings:
  - `text[char_start:char_end]` equals the chunk text.
  - No chunk crosses a section or span boundary.
  - No table is split.
  - Only table chunks exceed 800 tokens.
  - Every non-whitespace character of every section is in at least one chunk. (The user accepted this wording on 2026-10-03; whitespace between chunks and at section edges may be left out.)

## 3b detailed plan (agreed 2026-10-03, before the user left)

Measured on the 30 cached filings: 1.55M tokens. 104 tables are over 800 tokens, none over 2,000 (largest 1,825). No paragraph is over 800 tokens (largest 728).

Module `ingest/chunker.py`, pure Python, no database:
- `Chunk`, a frozen model: `chunk_id`, `cik`, `accession_no`, `form_type`, `fiscal_period`, `section`, `char_start`, `char_end`, `ordinal`, `token_count`, `tokenizer`, `chunker_version`, `contains_table`.
- `chunk_filing(filing) -> tuple[Chunk, ...]` chunks every section, the preamble included, in document order.

Rules:
1. **Blocks.** Each section span is cut into blocks.
   - Table spans from `find_table_spans` are one block each, glitch prose included (user decision 3b-1).
   - The text between tables splits into paragraphs at blank lines.
   - Block offsets are trimmed of surrounding whitespace.
2. **Fallback splitting.** A paragraph over 800 tokens is split at sentence ends. Edgartools escapes periods as `\.`, so sentence ends must handle that. A sentence over 800 tokens is split at whitespace.
3. **Packing.** Blocks are packed greedily into a chunk while the chunk's exact token count, the text from its first block to its last, stays at 800 or under. A table that doesn't fit starts a new chunk, and a table over 800 tokens is a chunk by itself.
4. **Overlap** (user decision 3b-2).
   - A new chunk starts up to 100 tokens back, at a sentence or paragraph start in the text blocks before it.
   - Overlap never comes from a table, so a chunk after a table starts at the table's end.
   - For a paragraph chunk, overlap shrinks to keep the chunk at 800 or under.
   - A table chunk may take up to 100 tokens of overlap (usually its caption) even when that makes it oversized.
5. **Boundaries.** No chunk crosses a section span. Multi-span items are chunked span by span, keeping their label.
6. **IDs and versions.**
   - `chunk_id = {accession_no}:{ordinal:04d}`, with `ordinal` counted across the whole filing.
   - `tokenizer = "cl100k_base"` and `chunker_version = "1"`.
   - `contains_table` is true when the chunk holds any table block (user decision 3b-3).

Acceptance, on the 6 fixtures (unit tests) and on all cached filings (local tests):
- `text[char_start:char_end]` is the chunk text, and `token_count` equals tiktoken's count of it.
- No chunk crosses a section span. No table span is split across chunks.
- Only chunks with `contains_table` exceed 800 tokens.
- Every non-whitespace character of every section is in at least one chunk.
- Overlap between consecutive chunks is 100 tokens or less and never starts inside a table.
- Chunk IDs are unique, ordered and the same on a rerun.
- The corpus report gains per-filing chunk counts: chunks, chunks with tables, oversized chunks and maximum tokens.

Autonomy (user decision 3b-4): run the whole workflow, commit locally after the reviews and verification pass, don't push, then stop and report.

## 3c: Postgres and resolve()
- A `filings` table holds metadata, the full parsed text and `financial_statements_section`, which is `part_iv_item_15` when Item 8 is only a pointer.
- A `chunks` table holds the spec fields, with a foreign key to `filings`. There's no vector column; that comes in Step 5.
- The schema is in `db/schema.sql`, applied by a script that's safe to rerun. No migration dependency.
- `resolve(chunk_id)` uses SQL `substr` on the filing text. Chunk text isn't stored separately.
- The loader writes `data/parsed/{accession_no}.json`.
- Tests: integration tests run against the CI Postgres service (localhost only). One resolves a random sample of 20 chunks, as the RUNBOOK asks, and one resolves every chunk.

## 3c detailed plan (agreed 2026-10-03)

The user approved the schema below explicitly on 2026-10-03, along with the commit policy: commit locally after reviews and verification pass, and don't push.

Facts behind it:
- `EdgarClient.list_filings()` is not cached; it calls EDGAR each time.
- The 30 cached filings hold 6.9 MB of text, with no NUL characters and no characters outside the BMP, so Python string indexes and Postgres `substr` positions agree.

Pipeline (user decision 3c-1, two steps):
- `python -m ingest.corpus`, which already selects the manifest over the network, also writes `data/parsed/{accession_no}.json`. Each file holds the `FilingMeta` and the `ParsedFiling` (text and sections).
- `python -m ingest.load` reads `data/parsed/*.json` offline, chunks each filing, and loads Postgres.
- Each filing is loaded in one transaction: upsert the filing row, delete its chunks, insert the new chunks. A rerun gives the same rows.

Schema, in `db/schema.sql`, applied by a script that's safe to rerun (`CREATE ... IF NOT EXISTS`):
- `filings`:
  - `accession_no` (primary key), `cik`, `company_name`, `form_type`, `fiscal_period`, `report_date`, `filing_date`, `primary_document`;
  - `parsed_text` and `text_sha256`;
  - `financial_statements_section`: `part_iv_item_15` when a 10-K's Item 8 is the verified pointer, else `part_ii_item_8` for a 10-K or `part_i_item_1` for a 10-Q;
  - `parser_commit` and `loaded_at`.
- `chunks`:
  - `chunk_id` (primary key), and `accession_no` referencing `filings` with ON DELETE CASCADE;
  - `cik`, `form_type`, `fiscal_period`, `section`, `char_start`, `char_end`, `ordinal`, `token_count`, `tokenizer`, `chunker_version`, `contains_table`;
  - CHECK (`char_end > char_start`, `char_start >= 0`) and UNIQUE (`accession_no`, `ordinal`).
- Indexes: `chunks(accession_no)` and `chunks(cik, fiscal_period, form_type)`.
- Added after the database review, with the user's approval (2026-10-03): CHECK `chunk_id = accession_no || ':' || lpad(ordinal, 4, '0')`, CHECK `chunks.form_type IN ('10-K', '10-Q')`, and CHECK that `text_sha256` is the SHA-256 of `parsed_text`. `STORAGE EXTERNAL` was declined.
- No vector column (Step 5). No sections table (user decision 3c-3; propose later if the interface or evals need one).

Code:
- `ingest/store.py`, a repository over a psycopg 3 connection, with every query parameterized:
  - `apply_schema(conn)`;
  - `load_filing(conn, meta, filing, chunks)`;
  - `get_chunk(conn, chunk_id)`;
  - `resolve(conn, chunk_id) -> str`, which runs `substr(parsed_text, char_start + 1, char_end - char_start)` and raises `ChunkNotFound` for an unknown ID.
- `DATABASE_URL` comes from the environment and is validated at startup. It is never logged.

Tests (user decision 3c-2):
- Each test session creates a throwaway schema in the `DATABASE_URL` database, applies `db/schema.sql` there and drops it afterwards.
- The database tests skip when `DATABASE_URL` is unset. A guard test fails when `CI` is set but `DATABASE_URL` is not, so the gate can't be skipped silently.

Stop condition (user decision 3c-4):
- `python -m ingest.load --verify` resolves a seeded random sample of 20 chunks, and every chunk, across the loaded filings. It checks each against `parsed_text[char_start:char_end]` from `data/parsed`, and writes `spikes/load_report.txt` with the seed and the commit.
- The fixture tests in CI do the same for the 6 fixtures: a seeded sample of 20, plus all chunks.

Acceptance:
- `resolve()` equals the parsed-text slice for every chunk of the 6 fixtures in CI, and of the 24 manifest filings in the verify report.
- A reload is idempotent: the same row counts and IDs.
- An unknown chunk ID raises `ChunkNotFound`.
- The schema applies twice without error.
- Foreign keys and checks reject bad rows.
- No secrets appear in logs.
- The database-reviewer agent reviews the SQL.

## Decisions (recommendation; user's answer, 2026-10-03)
1. Setup items from OPEN-DECISIONS. Recommendation: defer the ECC upgrade to after Step 3, because it renames workflow commands. Record the model (Opus 5.5) in BUILD-LOG, and run the context-budget check now. Answer: as recommended.
2. Tokenizer. Recommendation: tiktoken `cl100k_base`, already a dependency, with the tokenizer name stored on each chunk. Sizes are approximate until Step 5 picks the embedding model. Answer: as recommended.
3. Preamble. Recommendation: chunk it with `section='preamble'`, and decide in Step 5 whether to embed it. Answer: as recommended.
4. `ParsedTable.section_key` and the `doc.tables` list. Recommendation: remove them from the chunk path, because table positions now come from the text. This changes a model schema. Answer: as recommended.
5. Chunk IDs. Recommendation: deterministic IDs (`{accession_no}:{ordinal:04d}`), plus a `chunker_version` column. Freeze the chunker settings before Step 4 writes gold chunk IDs. Alternative: store gold evidence as character ranges, which survive re-chunking. Answer: as recommended.
6. Schema approval for the `filings` and `chunks` tables in 3c. Answer: as recommended.

## Risks
- High: changing chunker settings after Step 4 changes chunk IDs and invalidates gold labels (decision 5).
- Medium: Python string indexes and Postgres `substr` must count the same characters. Test on non-ASCII text.
- Medium: tables of up to 1,825 tokens. Step 5 must check the embedding model's input limit.
- Low: some non-required INTC section labels are wrong (known from Step 2b). Chunks inherit them.
