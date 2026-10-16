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

## Amendments

- **2026-10-05, clarification before any benchmark run (user decision).** "A hard negative is retrieved above a gold chunk" means above at least one gold chunk. A gold chunk that was not retrieved ranks below every retrieved chunk, so on a question that misses a gold chunk, any retrieved hard negative counts. A hard negative cited anywhere in the raw answer, kept or dropped, also counts. Why: a confusable chunk retrieved while gold evidence is missing is the failure H3 is about. The stricter reading (above the best-ranked gold chunk) can be recomputed from the ranks in each question record. Hypotheses, thresholds and the class-to-arm map are unchanged.

- **2026-10-16, Step 13 grilling, before any `test` run (user decisions).**
  - *Router rows.* The router is scored under the pre-registered class-to-arm map above, which stays the headline router row and the map H6 refers to. A second row uses a dev-best map: for each answerable class, the arm with the highest dev recall@10, a statistical tie (paired interval including 0) going to the cheaper arm. It is always labelled "tuned on dev". An oracle router (gold class through the pre-registered map) is reported beside them as a diagnostic. Why: every graph arm lost to `vector` on dev in its own class, so the pre-registered map is likely to lose; the second row shows what a router built from that evidence does, without replacing the test that was fixed in advance.
  - *Judge calibration.* "Cohen's kappa ≥ 0.6 against ~60 hand-scored answers" becomes a statistic per score type, since kappa does not fit 0–1 scores: Cohen's kappa ≥ 0.6 for citation support; Spearman's rho ≥ 0.6, with mean absolute error and signed bias, for faithfulness and answer relevancy; percent agreement with the disagreement count for decline and not-found correctness. Pass is read on the point estimate, with the 95% interval printed. The sample is 30 dev answers scored by the user, with claims split by an agent call separate from the judge, labelled as such; at n = 30 a rho of 0.6 has an interval of about [0.31, 0.79]. A column that fails leaves the headline table, as the rule above says for faithfulness. A corruption test (planted errors of four kinds in 30 dev answers) is added, reported as catch rates; it is not calibration. Why: the statistic change follows docs/research/router-langgraph-and-judge-calibration.md; the sample size is the user's time budget.
  - *`graph_global` cells.* On `test`, `graph_global` is run in full on global, decline and unanswerable questions and retrieval-only on lookup, local and multi_hop, whose judged cells are marked "retrieval only". Why: the user capped Step 13 spend at $10, and this arm is built for global questions.
  - *H5.* Latency is tested on a per-question end-to-end total, which the harness did not record before Step 13. The threshold is unchanged.
