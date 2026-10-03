# Step 3 plan: chunking

Status: approved 2026-10-03. The user accepted all six recommendations.

Fixed point for /step-review: set per sub-step when it starts.
- 3a: (not started)
- 3b: (not started)
- 3c: (not started)

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
  - Every character of every section is in at least one chunk.

## 3c: Postgres and resolve()
- A `filings` table holds metadata, the full parsed text and `financial_statements_section`, which is `part_iv_item_15` when Item 8 is only a pointer.
- A `chunks` table holds the spec fields, with a foreign key to `filings`. There's no vector column; that comes in Step 5.
- The schema is in `db/schema.sql`, applied by a script that's safe to rerun. No migration dependency.
- `resolve(chunk_id)` uses SQL `substr` on the filing text. Chunk text isn't stored separately.
- The loader writes `data/parsed/{accession_no}.json`.
- Tests: integration tests run against the CI Postgres service (localhost only). One resolves a random sample of 20 chunks, as the RUNBOOK asks, and one resolves every chunk.

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
