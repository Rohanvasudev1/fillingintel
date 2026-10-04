# Brief for subagents drafting eval questions (Step 4)

You are drafting candidate eval questions for FilingIntel, a citation-grounded Q&A system over SEC 10-K and 10-Q filings for NVDA, AMD and INTC. Your drafts are labelled `agent_drafted`. A human reviews each one before it can count, so make every draft worth a human's time. Honesty matters more than volume: a correct, hard, well-grounded question beats three easy ones.

## Hard rules
1. Use only chunk IDs that you found and read with the search tool. Never invent an ID.
2. The `gold_answer` must be fully supported by the text of the gold chunks. Copy numbers exactly as written, with units and periods. If the chunks don't support an answer, don't write the question.
3. Write only your own output file, `eval/drafts/<your-slice>.jsonl`. Don't edit any other file in the repo, run git, or touch the database.
4. Don't write investment advice, except in the `decline` slice, where the question asks for it and the gold answer refuses.
5. Run the validator on your file until it reports 0 problems.

## Tools (run from the repo root)
- Search: `uv run python -m eval.search <words> [--ticker NVDA|AMD|INTC] [--period FY2025-Q2] [--section part_i_item_1a] [--limit 20]`. Every word must appear in the chunk.
- Show one chunk's exact text and SHA-256: `uv run python -m eval.search --show <chunk_id>`.
- Validate your file: `uv run python -m eval.validate eval/drafts/<your-slice>.jsonl`.
- Coverage: `uv run python -m eval.coverage eval/drafts/<your-slice>.jsonl`.

## The corpus
NVDA's fiscal year ends in late January, so its labels run a year ahead of AMD's and INTC's.
- **NVDA:** FY2025 and FY2026 10-Ks; FY2025-Q1 to Q3 and FY2026-Q1 to Q3 10-Qs.
- **AMD and INTC:** FY2024 and FY2025 10-Ks; FY2024-Q1 to Q3 and FY2025-Q1 to Q3 10-Qs.

Useful sections:
- 10-K: `part_i_item_1` (business), `part_i_item_1a` (risk factors), `part_ii_item_7` (MD&A), `part_ii_item_8` (financial statements; NVDA's are in `part_iv_item_15`).
- 10-Q: `part_i_item_1` (financial statements), `part_i_item_2` (MD&A), `part_ii_item_1a` (risk factors).

## Record format (one JSON object per line)
```json
{"id": "q0001", "question": "...", "class": "lookup", "gold_answer": "...",
 "gold_chunk_ids": ["0001045810-26-000021:0042"],
 "gold_text_sha256": {"0001045810-26-000021:0042": "<sha256 printed by --show>"},
 "hard_negatives": [{"chunk_id": "0001045810-25-000023:0040", "relation": "same_company_other_period"}],
 "tickers": ["NVDA"], "fiscal_periods": ["FY2026"],
 "topic": "financials", "difficulty": "medium", "reasoning": "quantitative",
 "evidence_scope": "single", "provenance": "agent_drafted",
 "author": "claude-subagent:<your-slice>", "split": "dev", "notes": "..."}
```

Field rules:
- `id`: number your records q0001, q0002 and so on. They are renumbered when the slices are merged.
- `class`: `lookup` (a single fact in one chunk), `local` (one entity, needing the surrounding context), `multi_hop` (joining facts that never appear in the same chunk), `global` (reasoning across the corpus), `decline` (investment advice the system must refuse), or `unanswerable` (not disclosed in these filings).
- `tickers` and `fiscal_periods`: exactly the set of tickers and periods of your gold chunks. The validator checks this. For `decline` and `unanswerable`, use the companies and periods the question is about.
- `evidence_scope`: `none` for 0 gold chunks, `single` for 1, `two` for 2, `multi` for 3 or more. `decline` and `unanswerable` have no gold chunks, so they use `none` and an empty `gold_text_sha256`.
- `hard_negatives`: 1–2 per answerable question, never a gold chunk. Pick the most confusable chunk: the same metric in another period (`same_company_other_period`), a nearby passage in the same filing (`same_company_same_filing`), or a peer saying something similar (`peer_company`).
- `topic`: one of `financials`, `segments`, `risk`, `supply_chain`, `competition`, `legal_regulatory`, `capital_allocation`, `products_strategy`, `operations`, `governance`.
- `difficulty`: `easy`, `medium` or `hard`. `reasoning`: `quantitative` or `qualitative`.
- `split`: always `"dev"`. It is recomputed at the merge.
- `notes`: one line on why the question is good, and on anything a reviewer should check, such as a calculation you did.

## What makes a good question
- Ask what an analyst would ask, phrased naturally. Name the company and period unless the class needs otherwise (a `global` question spans all three companies).
- `multi_hop`: the answer must need two or more chunks that are in different filings or different sections, for example comparing a risk factor across years, joining a segment's revenue with its stated drivers, or comparing two companies. A Ctrl-F user should not find the answer in one place.
- `global`: at least one gold chunk per company, 3 or more chunks in all, and a theme rather than a number.
- Prefer questions where a plausible wrong answer exists in another period or company. That's what the hard negatives test.
- Avoid questions answerable from the cover page or the table of contents, near-duplicates of each other, and yes/no questions.
