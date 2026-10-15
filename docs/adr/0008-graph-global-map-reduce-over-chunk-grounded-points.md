# graph_global answers by map-reduce over map points that cite chunks

`graph_global` is the one arm that does not end at `answer/v1`. It ranks lower-level community reports by exact cosine against the question's embedding and keeps the top N communities that have evidence in the question-filtered filings. Each of those communities gets one map call, which sees its report and its 6 evidence chunks closest to the question and writes map points scored 0–100, each citing `[chunk_id]`. A reduce call writes the final answer from the points scored above 0, with `answer/v1`'s status line and fixed sentences. The existing citation filter keeps a sentence only when every chunk it cites was shown to a map call for that question. Community reports rank communities and feed the map step, but they are never cited: an answer cites chunks, so invariant 3 holds without a report-to-chunk rewrite.

For retrieval metrics the arm's ranked list is the chunks shown to map calls, ordered by community rank and then by cosine within each community, with duplicates removed. Retrieval is therefore free to compute, and N can be tuned without model calls.

Decided on 2026-10-15 in the Step 12 grilling. Research: docs/research/communities-and-global-search.md.

## Considered options
- **Chunk handoff, as in ADR-0007.** Rank communities, collect their chunks, cut to 10 and send them to `answer/v1`. Only chunk choice would differ from `vector`, but there would be no map-reduce, which is the method the RUNBOOK and H4 (judged answer relevancy) describe. The research note recommended this option. It remains a cheap labelled ablation.
- **GraphRAG as published.** Map over report text and cite report IDs. Reports cite entity and relationship records, nothing checks the citation, and a sentence would trace to text only through two hops. This breaks invariant 3.
- **A cheap model rates every report (dynamic community selection).** Closer to current GraphRAG, but it costs calls on every question, and embedding rank is deterministic.

## Consequences
- `graph_global` differs from `vector` in both retrieval and answer writing. Its differences from the control arm cannot be attributed to retrieval alone, and the write-up says so.
- The map and reduce prompts are versioned files (`global_map/v1`, `global_reduce/v1`), recorded in the run header like `answer/v1`. Both use the answer model, Sonnet 5.5 at effort `high`, so a difference comes from the method and not the model.
- Cost scales with N: about $0.20 per question at N = 8. Step 12's full runs cover only global, decline and unanswerable questions.
- Path-level citations and a new answer prompt for the other graph arms stay in Step 13 (ADR-0007).
