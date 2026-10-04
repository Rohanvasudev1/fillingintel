# FilingIntel research plan — DRAFT, not approved

Status: draft written by the assistant on 2026-10-04, for the user's grilling session. Hypotheses become binding only once the user approves them and this file moves to `docs/RESEARCH-PLAN.md` with a date. They must be fixed before any benchmark run (OPEN-DECISIONS, Step 4). Every threshold below is a proposal.

## Question
On SEC 10-K and 10-Q filings for three semiconductor companies, when does graph-based retrieval (local neighbourhood, Cypher traversal, community summaries) beat plain vector retrieval, and what does it cost? The point is an honest map of wins and losses, not a demonstration that graphs win.

## Setup fixed before measuring
- **Corpus:** the 24-filing manifest (Step 2b), chunked by chunker_version 1 (Step 3). Chunk IDs are frozen for the benchmark.
- **Eval set:** `eval/eval_set.jsonl`, human-written or human-verified, about 120 records. Agent-drafted records that the user hasn't verified are reported only as a separate, labelled set.
- **Split:** about 30% `test` by hash. All tuning (prompts, k, the router) uses `dev` only, and `test` is scored once, for the final table.
- **Arms:** `vector` (the control), `graph_local`, `graph_traversal`, `graph_global`, and the router.
- **Metrics per arm and class:**
  - context recall and precision against the gold chunks;
  - a wrong-evidence rate: how often a hard negative is retrieved above a gold chunk, or cited;
  - faithfulness and answer relevancy (LLM judges, calibrated against hand-scored answers);
  - citation validity;
  - p50 and p95 latency, and cost per query.
- **Statistics:** bootstrap 95% confidence intervals on every cell, and paired comparisons against `vector` on the same questions. A difference counts only when its interval excludes 0.

## Hypotheses (proposed; at least one predicts a graph loss)
1. **H1, vector holds on lookups.** On `lookup` questions, the `vector` arm's context recall@5 is within 0.05 of every graph arm's, or better.
2. **H2, traversal helps on joins.** On cross-company `multi_hop` questions, `graph_traversal` beats `vector` on context recall@10 by at least 0.10.
3. **H3, period confusion is the main failure.** For every arm, most wrong-evidence errors on answerable questions involve `same_company_other_period` hard negatives.
4. **H4, global questions favour community summaries.** On `global` questions, `graph_global` beats `vector` on judged answer relevancy by at least 0.10.
5. **H5, graphs cost more.** Every graph arm's p95 latency is at least 2x `vector`'s, and its cost per query is higher.
6. **H6, the router is useful but imperfect.** The router picks the arm matching the gold class on at least 80% of `dev` questions, and declines every `decline` question.

## Things that would change the plan
- If the human set ends up far from the class targets, report per-class sample sizes and widen intervals rather than pooling.
- If judge calibration (Cohen's kappa against hand scores) comes out below 0.6, report only the retrieval metrics for faithfulness and leave the judged columns out.

## Threats to validity, stated up front
- Only three companies, all in one sector. The findings may not transfer to other industries.
- The question author also designed the system, which is partly mitigated by the held-out `test` split.
- Agent-drafted questions may be easier, or differently phrased, than human ones. That's why they're reported separately.
