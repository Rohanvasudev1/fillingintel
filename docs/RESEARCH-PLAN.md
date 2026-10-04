# FilingIntel research plan

Approved by the user on 2026-10-04 in the Step 4 grilling session, before any benchmark run. The hypotheses, thresholds and class-to-arm map below are fixed. Changing them after a run needs a dated amendment at the end of this file that says why, and both versions are reported.

## Question
On SEC 10-K and 10-Q filings for three semiconductor companies, when does graph-based retrieval (local neighbourhood, Cypher traversal, community summaries) beat plain vector retrieval, and what does it cost? The point is an honest map of wins and losses, not a demonstration that graphs win.

## Setup fixed before measuring
- **Corpus:** the 24-filing manifest (Step 2b), chunked by chunker_version 1 (Step 3). Chunk IDs are frozen for the benchmark.
- **Eval set:** the user decided on 2026-10-04 to use the 140 agent-drafted questions in `eval/agent_drafted_set.jsonl`, without human review: 40 lookup, 35 local, 30 multi_hop and 15 global, plus 10 decline and 10 unanswerable. Every table and claim names them as agent-drafted. If human-written or human-verified records are added later, they get their own column.
- **Split:** about 30% `test` by hash. All tuning (prompts, k, the router) uses `dev` only, and `test` is scored once, for the final table.
- **Arms:** `vector` (the control), `graph_local`, `graph_traversal`, `graph_global`, and the router.
- **Metrics per arm and class:**
  - context recall and precision against the gold chunks;
  - a wrong-evidence rate: how often a hard negative is retrieved above a gold chunk, or cited;
  - faithfulness and answer relevancy (LLM judges, calibrated against hand-scored answers);
  - citation validity;
  - p50 and p95 latency, and cost per query.
- **Statistics:** bootstrap 95% confidence intervals on every cell, and paired comparisons against `vector` on the same questions. A difference counts only when its interval excludes 0.
- **Class counts:** the four main classes hold the RUNBOOK's 120 questions (40/35/30/15). The 10 `decline` and 10 `unanswerable` questions are extra and reported in their own rows.
- **External check (appendix):** after the vector baseline, the `vector` arm is run on FinRank's pooled corpus (5,230 passages, CC BY-NC 4.0) and compared with its published Recall@10 baselines. The result is reported in an appendix, not in the main table.

## Hypotheses (at least one predicts a graph loss)
1. **H1, vector holds on lookups.** On `lookup` questions, the `vector` arm's context recall@5 is within 0.05 of every graph arm's, or better.
2. **H2, traversal helps on joins.** On `multi_hop` questions, `graph_traversal` beats `vector` on context recall@10 by at least 0.10. The cross-company subset is reported as a secondary breakdown.
3. **H3, period confusion is the main failure.** For every arm, most wrong-evidence errors on answerable questions involve `same_company_other_period` hard negatives.
4. **H4, global questions favour community summaries.** On `global` questions, `graph_global` beats `vector` on judged answer relevancy by at least 0.10.
5. **H5, graphs cost more.** Every graph arm's p95 latency is at least 2x `vector`'s, and its cost per query is higher.
6. **H6, the router is useful but imperfect.** The router picks the arm matching the gold class on at least 80% of `dev` questions, and declines every `decline` question. The expected arm per class, fixed now:
   - `lookup` → `vector`
   - `local` → `graph_local`
   - `multi_hop` → `graph_traversal`
   - `global` → `graph_global`
   - `decline` → decline
   - `unanswerable` → any arm, counted correct when the answer says the filings don't contain the information

## Things that would change the plan
- Report per-class sample sizes and widen intervals rather than pooling. The `test` split holds 13 lookup, 13 local, 7 multi_hop (4 cross-company), 5 global, 4 decline and 5 unanswerable questions.
- Any class with fewer than 10 `test` questions (multi_hop, global, decline, unanswerable) is reported as "directional". It counts as confirmed only when its interval excludes 0.
- If judge calibration (Cohen's kappa against hand scores) comes out below 0.6, report only the retrieval metrics for faithfulness and leave the judged columns out.

## Threats to validity, stated up front
- Only three companies, all in one sector. The findings may not transfer to other industries.
- The questions were drafted by agents working in the same project that built the system, which is partly mitigated by the held-out `test` split.
- The RUNBOOK's friend Ctrl-F test, a check that multi-hop questions aren't too easy, was skipped on 2026-10-04. It can still be run before the final benchmark.
- **The questions are agent-drafted and not human-reviewed.**
  - Subagents wrote both the questions and the gold answers, after reading the same chunks a retriever will search. That may favour retrieval-friendly phrasing and easy evidence.
  - Some drafts carry reviewer notes that were never checked: figures that don't reconcile across filings, and a table header that may be a parser artefact.
  - Results should be read as "on agent-written questions" and not generalised to analyst questions.
