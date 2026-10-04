# Step 4 plan: eval set

Status: approved 2026-10-04. The user asked for Step 4 to run to completion unattended ("go ahead with step 4 however you see fit, don't stop until you've finished"), with subagents creating a question set.

Fixed point for /step-review: ed8ec01.

Later chat instruction from the user (2026-10-04): "keep going until step 4 is done". The drafting subagents were launched in batches of three after a rate-limit failure. The python-review findings were fixed test-first. The split check was switched on after all slices finished, because drafts carry split "dev" until the merge recomputes it.

## Spec (docs/RUNBOOK.md, Step 4)
- About 120 questions: about 40 `lookup`, 35 `local`, 30 `multi_hop` and 15 `global`.
- Each entry has `question`, `class`, `gold_answer`, `gold_chunk_ids` and `notes`.
- The questions are "written by Rohan, not by a coding agent. Do not generate these with an LLM." A coding agent may build tooling (a schema validator, and a check that every gold chunk ID resolves), but never the questions or answers.
- Stop condition: `eval/eval_set.jsonl` with 120 labelled entries, committed. Have a friend try five multi-hop questions with ctrl-F.

## The user's instruction and invariant 1
The user asked for subagents to create a question set. That conflicts with invariant 1 and the RUNBOOK. It is the user's project and their explicit instruction, so it is followed in the form docs/OPEN-DECISIONS.md proposed:
- Agent-drafted questions go only in `eval/agent_drafted_set.jsonl`, with `provenance: "agent_drafted"`. They are never written to `eval/eval_set.jsonl`, and results on them are always reported separately.
- A record becomes `human_verified` only when the user verifies it with `python -m eval.review`. Agents never set that value or edit verified records.
- Invariant 1 in CLAUDE.md is reworded to say this. The RUNBOOK gets an amendment note; its stop condition (a human set) is unchanged.
- So Step 4 can be finished up to the human part. The tooling, the drafted set, the research-plan draft and the grilling list are done. The human-written or human-verified 120 entries are the user's work, and the stop condition is met only then.

## FinRank (searched 2026-10-04)
Sources: github.com/datanxt/FinRank and arXiv 2608.07400.
- 1,185 manually authored records over 22 US companies in pharmaceuticals, oil & gas and automotive (10-K and 10-Q filings, 2024–2025). No NVDA, AMD or INTC. License CC BY-NC 4.0: academic use with attribution.
- Decision: it is not used as our question set. It has no gold evidence in our corpus, and adding its companies is a scope change (CLAUDE.md: "propose, don't build").
- Borrowed design:
  - per-record `topic`, `difficulty`, `reasoning` (quantitative or qualitative) and `evidence_scope`;
  - hard negatives labelled by relation (`same_company_other_period`, `same_company_same_filing`, `peer_company`);
  - a hash of each gold passage's text, so gold labels break loudly if chunking changes.
- Proposed for Steps 5–6 (OPEN-DECISIONS): an external check of our retriever on FinRank's 5,230-passage corpus against its published baselines.

## Decisions (the assistant's recommendations, adopted under the user's "however you see fit")
1. Our own set is the main set. FinRank is not used as questions.
2. Hard negatives: 1–2 per question, labelled by relation and chosen to be confusable (another period of the same company first, then a peer company). A wrong-evidence rate is reported per arm.
3. Invariant 1 is reworded as above.
4. Agent-drafted questions are allowed only in the separate file. They are never counted as human until verified.
5. `docs/RESEARCH-PLAN.md`: a dated draft of falsifiable hypotheses, at least one predicting a graph-arm loss, marked DRAFT for the grilling session. The ECC hook blocks new .md files outside `.claude/plans/`, so the draft lives at `.claude/plans/research-plan-draft.md` and moves to `docs/` once the user approves.
6. Question mix: the RUNBOOK's 40/35/30/15, balanced across the three companies and both fiscal years. About 10 declined (investment-advice) questions and about 10 not-in-filings questions are added as separate classes (`decline` and `unanswerable`), so the refusal and faithfulness behaviour is measured.
7. Hold-out: about 30% of records get `split: "test"`, chosen by a seeded hash of the question text, and are not looked at until the final benchmark.
8. Setup items: the ECC upgrade is deferred until after Step 4's tooling; the model switch is the user's setting; grilling runs directly from the decision list, without installing the skill.

## Record schema (`eval/schema.py`, pydantic, frozen)
- `id`: `q0001`-style, unique within a file.
- `question`, `class` (`lookup`, `local`, `multi_hop`, `global`, `decline` or `unanswerable`), `gold_answer`, `notes`.
- `gold_chunk_ids`: one or more resolvable IDs, except none for `decline` and `unanswerable`.
- `gold_text_sha256`: a map from chunk ID to the SHA-256 of its resolved text.
- `hard_negatives`: 0–2 entries of `{chunk_id, relation}`. A hard negative is never also a gold chunk.
- `tickers`, `fiscal_periods`: what the question is about.
- `topic`, `difficulty` (`easy`, `medium` or `hard`), `reasoning` (`quantitative` or `qualitative`), `evidence_scope` (`single`, `two` or `multi`).
- `provenance` (`human_written`, `human_verified` or `agent_drafted`), `author`, `split` (`dev` or `test`).

## Tooling (built test-first)
- `python -m eval.validate <file>`: schema; unique IDs; gold and hard-negative chunk IDs exist; text hashes match. It checks offline against `data/parsed` plus the chunker, or with `--db` through `resolve()`. It refuses `agent_drafted` records in `eval/eval_set.jsonl`.
- `python -m eval.coverage <file>`: counts by class, ticker, period, provenance and split, against the RUNBOOK targets.
- `python -m eval.search "<terms>" [--ticker] [--period] [--section]`: keyword search over chunks, for authoring. `python -m eval.search --show <chunk_id>` prints the exact text (built into search, not as a separate `eval.show`).
- `python -m eval.review <file>`: walks the agent drafts one at a time. The user accepts (it becomes `human_verified` with the user as author, and moves to `eval/eval_set.jsonl`), rejects, or skips.
- Added during review:
  - `eval/merge_drafts.py` merges the slices.
  - `eval/review_log.py` keys decisions by `draft_key`, the hash of a draft's question and gold chunks.
  - `derived_from` holds that key.
  - `eval.validate` gains `--kind {auto,human,drafted}` and `--db`.
  - `eval.review` refuses non-interactive input.
- `eval/agent_drafted_set.jsonl`: written by subagents, each given a slice (a class and companies). Drafts must use real chunk IDs found with `eval.search`, answers must be supported by the quoted chunk text, and the validator must pass.

## Acceptance
- The validator, coverage, search, show and review tools are tested on the fixtures, offline in CI.
- `eval/agent_drafted_set.jsonl` passes the validator on the 24-filing corpus, every record is `agent_drafted`, and it covers the class mix.
- The research-plan draft and the grilling decision list exist.
- CLAUDE.md invariant 1, the RUNBOOK note and OPEN-DECISIONS are updated.
- Reviews done: python-review and /step-review. Verification loop run, BUILD-LOG written, commit pushed, CI green.
