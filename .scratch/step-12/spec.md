# Step 12: Communities and global search

Status: ready-for-agent
Sources: RUNBOOK Step 12 and the Step 11 outcome, docs/GRAPH-LAYER.md (indexing pipeline, global search), docs/RESEARCH-PLAN.md (H4, H5), docs/OPEN-DECISIONS.md (Step 11 close, the judging race), ADR-0001, ADR-0004 (amendment of 2026-10-15), ADR-0007, ADR-0008, and the grilling session of 2026-10-15. Every round (Q1–Q24) and the five test seams were answered "as recommended", except Q11, where the user asked for a lower spend cap and chose $6 with full runs on global, decline and unanswerable questions only. External research: docs/research/communities-and-global-search.md (Leiden libraries, determinism, GraphRAG community reports and global search, published evaluations, sparse graphs, cost). Vocabulary: CONTEXT.md (community graph, co-occurrence edge, community, derived node, community report, map point, arm, control arm, question filter, evidence).

## Problem Statement

Step 11 closed with both graph arms losing to `vector`, and losing most on global questions: dev recall@10 0.425 (`graph_local`) and 0.367 (`graph_traversal`) against `vector`'s 0.800. Global questions ask the same thing of all three companies ("How does dependence on external foundries, and on TSMC in particular, differ across NVIDIA, AMD and Intel…"), so there is no single entity to start from. RESEARCH-PLAN's H4 (community summaries beat `vector` on global judged answer relevancy by 0.10 or more) has no arm to test.

Three things stand in the way:
- **The graph has no communities.** On extracted edges alone, 943 of the 2,181 non-metric extracted nodes are singletons, because risk factors connect mostly to their filing and their own company's other filings. Leiden cannot group them into topics that span companies.
- **Nothing summarizes a group of entities.** GraphRAG's community reports cite entity and relationship records, and its global answers cite report IDs that nothing checks. That breaks invariant 3.
- **Global search can run away on cost.** Published GraphRAG global queries use hundreds of thousands of tokens. The user is cost-sensitive and capped Step 12 at $6.

The literature (Han et al., GraphRAG-Bench) predicts community search loses to vector RAG on questions that need specific per-entity evidence, which is what these questions are. With 10 dev global questions and `vector` at 0.800, any result is directional. A loss is a valid outcome and gets reported plainly.

## Solution

Build communities from a community graph: extracted nodes other than metric values, joined by their extracted edges and by co-occurrence edges (two nodes evidenced by one chunk). Leiden runs twice with fixed settings: once over the whole graph for the top level, and once inside each top community over 25 members for the lower level. On load run 4 this gives 34 top and 111 lower communities, identical across runs, and the TSMC community spans all three filers.

Haiku 5.5 writes a community report for each community of 3 or more members. A report cites chunks from its community's evidence, and uncited sentences are dropped. Communities, reports and membership are written to committed files and loaded into Neo4j as derived `:Community` nodes (ontology version 5) in the graph rebuild's single transaction.

The new arm, `graph_global`, ranks lower-level reports by cosine against the question, keeps the top N with evidence in the question filter, and sends each to a map call (Sonnet 5.5). The call sees the report and the community's 6 filtered chunks closest to the question, and writes map points scored 0–100 that cite chunks. A reduce call (Sonnet 5.5) writes the answer from the points, with `answer/v1`'s status line, and the existing citation filter drops any sentence citing a chunk no map call was shown (ADR-0008).

N is tuned on retrieval-only scores, which cost no model calls. The full runs cover the 10 dev global, 6 decline and 5 unanswerable questions and are compared with `vector` by `eval.compare`. A provenance check walks every kept citation back through community, member and `EVIDENCED_BY` to its chunk.

## User Stories

1. As the project owner, I want the judging race fixed before any paid run, so that a full run cannot fail mid-judging and need a rerun.
2. As the project owner, I want the fix to leave every score unchanged, so that past runs stay comparable.
3. As the project owner, I want the community graph built from the same pure builder output as the graph load, so that communities match the loaded graph without reading Neo4j.
4. As the project owner, I want metric values left out of the community graph, so that thousands of per-filing numbers do not form stars around segments and products.
5. As the project owner, I want companies, filings, periods and chunks left out, so that three company hubs do not pull every topic into per-company groups.
6. As the project owner, I want co-occurrence edges between nodes evidenced by the same chunk, so that risk factors join topics that span companies.
7. As the project owner, I want extracted edges weighted by their evidence chunk count and co-occurrence edges by their shared chunk count, so that well-evidenced links pull harder.
8. As the project owner, I want nodes with no edge left out of communities and counted, so that the gap is visible.
9. As the project owner, I want exactly two levels, top and lower, so that the RUNBOOK's "two resolution levels" holds and the hierarchy has an even depth.
10. As the project owner, I want the lower level made by running Leiden inside each top community over 25 members, so that lower communities are small enough to summarize.
11. As the project owner, I want a top community of 25 or fewer members to be its own lower community, so that every member has a lower community.
12. As the project owner, I want modularity at resolution 1.0 with a fixed seed and a pinned library version, so that the same graph gives the same communities on any run.
13. As the project owner, I want every community setting frozen before any question runs, so that communities are never fitted to the eval questions.
14. As the project owner, I want each community to carry a hash of its members, so that a report can be matched to exactly the community it was written for.
15. As the project owner, I want the communities written to a committed run file with commit, seed, settings and counts, so that anyone can see how they were made.
16. As the project owner, I want the community run file to list sizes per level, singletons left out, and communities too small for a report, so that the shape is visible.
17. As the project owner, I want a report for every community of 3 or more members, so that each selectable community has something to rank by.
18. As the project owner, I want communities of 1–2 members to get no report and never be selected, so that tiny groups cost nothing.
19. As the project owner, I want a lower report written from the members, their labels, their edges with evidence spans and the most-cited chunk texts, so that the report rests on evidence.
20. As the project owner, I want a top report written from its lower reports, so that a 200-member community fits the input budget.
21. As the project owner, I want every report sentence to cite a chunk from its community's evidence, with uncited or out-of-community sentences dropped and counted, so that reports cannot invent themes.
22. As the project owner, I want a top report to cite only chunks its lower reports kept, so that the same check holds at both levels.
23. As the project owner, I want reports written by Haiku 5.5 at effort `high` through the response cache, so that they are cheap and reruns cost nothing.
24. As the project owner, I want the report command to print its estimate and refuse to run above `--max-cost`, so that spend never surprises me.
25. As the project owner, I want reports written to a committed file keyed by member hash and report prompt version, so that the graph can be rebuilt offline.
26. As the project owner, I want the report prompt versioned and pinned by hash, so that a changed prompt is a new version.
27. As the project owner, I want the graph rebuild to load communities, membership and reports in its single transaction, so that a rebuild never loses them.
28. As the project owner, I want the rebuild to refuse a community with no report matching its member hash, so that stale reports never load.
29. As the project owner, I want Community as a derived node in ontology version 5, so that the schema text and validator know it.
30. As the project owner, I want the validator to check that every member is an extracted node other than a metric value, so that membership follows the community graph's rule.
31. As the project owner, I want the validator to check one parent per lower community, so that the two levels form a tree.
32. As the project owner, I want the validator to check every report citation against the members' evidence, so that a report's provenance is verified in the database.
33. As the project owner, I want the validator to recompute each member hash, so that a changed membership is caught.
34. As the project owner, I want the rebuild to upgrade a graph holding ontology version 4 and refuse a newer one, as in Step 11, so that versions move one way.
35. As a benchmark reader, I want `graph_global` to run through the same harness as the other arms, so that its results sit beside them.
36. As a benchmark reader, I want `graph_global` to apply the question filter, so that a question about one company and period only uses that company's and period's evidence.
37. As a benchmark reader, I want communities ranked by exact cosine between the question's embedding and the report embeddings, so that selection is deterministic and costs no model calls.
38. As the project owner, I want report embeddings in the existing cached-embedding path, with the arm refusing uncached reports, so that runs never embed silently.
39. As a benchmark reader, I want only communities with evidence in the filtered filings selected, so that a filtered question never maps an irrelevant community.
40. As a benchmark reader, I want each map call to see the report and the community's 6 filtered chunks closest to the question, so that the map step reads evidence and not just a summary.
41. As a benchmark reader, I want map points scored 0–100 and citing chunks, so that the reduce step can rank them and every claim stays grounded.
42. As the project owner, I want a map point citing a chunk its own call was not shown to be dropped and counted, so that points cannot borrow evidence.
43. As a benchmark reader, I want the reduce step to use points above 0, sorted by score, under about 8k tokens, so that the answer draws on the most relevant points.
44. As a benchmark reader, I want the reduce output to carry `answer/v1`'s status line and fixed sentences, so that decline and not-found behave as in every other arm.
45. As the project owner, I want no reduce call when no point scores above 0, with the answer `not_found`, so that empty questions cost no reduce.
46. As a benchmark reader, I want the citation filter to keep only sentences whose citations were all shown to a map call for that question, so that invariant 3 holds.
47. As a benchmark reader, I want map and reduce on Sonnet 5.5 at effort `high`, like the answer model, so that a difference from `vector` comes from the method, not the model.
48. As the project owner, I want map calls run 4 at a time with failures stopping the question cleanly, so that runs are fast and failures are visible.
49. As a benchmark reader, I want the arm's ranked chunk list to be the chunks shown to map calls, ordered by community rank and then cosine, so that recall is comparable and free to compute.
50. As a benchmark reader, I want cited chunks counted for the citation metrics, so that answer grounding is measured as for other arms.
51. As the project owner, I want tokens and cost logged per question for `community_select`, `global_map` and `global_reduce`, so that I see where cost goes.
52. As the project owner, I want those three stages as tracing spans, with prompt text only under `FILINGINTEL_TRACE_TEXT`, so that Phoenix shows the fan-out without leaking text by default.
53. As the project owner, I want the run header to record the report, map and reduce prompt versions, N, the community run and the ontology version, so that every score is traceable to its config.
54. As the project owner, I want N chosen from {4, 6, 8, 12} on the 10 dev globals' retrieval-only recall@10, ties to the smaller, so that the fan-out is tuned without spend.
55. As the project owner, I want the N file to include all 82 answerable questions' recall, so that the choice's effect on other classes is visible.
56. As a benchmark reader, I want the N choice labelled "tuned on dev, n = 10", so that I read it as tuned.
57. As a benchmark reader, I want a retrieval-only ablation that maps top-level communities, so that I see whether the lower level was the right choice.
58. As the project owner, I want full dev runs only on the 10 global, 6 decline and 5 unanswerable questions, so that Step 12 stays under $6.
59. As the project owner, I want an estimate before every paid run and a refusal above the remaining cap, so that spend stays under $6 in total.
60. As a benchmark reader, I want `graph_global` compared with `vector` by `eval.compare` on the global questions, so that the difference has a paired interval.
61. As a benchmark reader, I want H4 reported with its interval and labelled directional, so that a small-sample result is not oversold.
62. As a benchmark reader, I want a provenance check that walks every kept sentence's citation through community, member and `EVIDENCED_BY` to its chunk, so that "traceable provenance" is a measured property, not a claim.
63. As the project owner, I want the provenance check to fail with a distinct exit code on any break, so that it can gate the step.
64. As the project owner, I want `test` left untouched, so that the held-out split stays unseen until Step 13.
65. As a reader of the write-up, I want the RUNBOOK, BUILD-LOG, README and CLAUDE.md updated with the outcome, so that the result is recorded where people look.

## Implementation Decisions

**Ticket 01, the judging race (harness change, approved).** The OpenAI `Response` model is rebuilt once at import of the OpenAI judge backend, so judge threads never build it concurrently. A test imports the backend and parses a recorded reply from several threads. Scores are unchanged; the OPEN-DECISIONS entry is removed.

**Community graph (pure, offline).** A pure function takes the graph builder's output (the same nodes and edges `graph.load` writes) and returns the community graph: extracted nodes other than MetricValue, extracted edges between them weighted by `len(chunk_ids)`, and co-occurrence edges weighted by the number of chunks two nodes share as evidence, summed into one undirected weight per pair. Company, Filing, Period and Chunk nodes are left out, so an edge with a Company end does not count. Self-loops are dropped. Nodes with no edge are returned separately.

**Communities (pure, offline).** A deep module `communities` takes the community graph and settings and returns communities: Leiden through `graspologic-native==1.3.1` (`leiden`, modularity, resolution 1.0, randomness 0.001, one iteration, fixed seed) over the whole graph for the top level, then the same call inside each top community over 25 members for the lower level. A top community at or under 25 is its own lower community. Each community gets a deterministic `community_id` (level plus a short member hash), its `member_hash` (SHA-256 over sorted member keys) and its parent. Communities under 3 members are kept in the file and marked `reportable: false`. The settings live in one frozen config object whose hash goes in the run file. `graspologic-native` is the only new dependency.

**`python -m graph.communities`** (offline) reads what `graph.load` reads, runs the builder, the community graph and the communities, and writes `benchmarks/communities/communities-run{N}.json` with commit, settings and config hash, library version, seed, counts per level, size distribution, the singletons left out and every community's members.

**Reports.** `prompts/report/v1.md`, pinned by SHA-256, version string `report/v1@<hash8>` covering system prompt, user template and output schema. Structured output `{title, summary, findings: [{text}]}` where `summary` and each finding cite `[chunk_id]`. Lower-level input: member keys, labels and names; edges among members with their evidence spans; the texts of the most-cited member chunks (by how many members and edges cite them) until about 6k input tokens. Top-level input: its lower reports' titles, summaries and findings. The citation check keeps a sentence only when it cites at least one chunk and every cited chunk is in the community's evidence (lower: the members' evidence chunks; top: chunks kept in its lower reports); dropped sentences are counted per report. `report_chunk_ids` is the set of chunks the kept sentences cite. Model claude-haiku-5-5 at effort `high` with structured output through the existing Anthropic client and response cache, price table 2026-10-10. Lower reports are written before top reports.

**`python -m graph.reports`** (network) checks `ANTHROPIC_API_KEY`, refuses while `ANTHROPIC_BASE_URL` is set, prints an estimate, refuses above `--max-cost` (default $1) or above the Step 12 cap counting committed spend, runs 4 calls at a time, and writes `benchmarks/communities/reports-run{N}.jsonl` (one record per reportable community: `community_id`, `member_hash`, level, title, kept report text, `report_chunk_ids`, dropped-sentence count, prompt version, tokens, cost) plus a summary header.

**Ontology version 5 (ADR-0004 amendment).** `:Community` is a derived label with `community_id` (key), `level` (`top` or `lower`), `member_hash`, `title`, `report`, `report_chunk_ids`, `report_prompt`, `ontology_version`. `IN_COMMUNITY` from an extracted node other than MetricValue to a lower Community; `PARENT_OF` from a top Community to a lower one. Derived edges carry `ontology_version` only, not evidence lists; invariant 4 applies to extracted edges, and a derived node's provenance is its members' evidence. Uniqueness constraint on `Community.community_id`. The schema text and hash change.

**Loading.** `graph.load --rebuild` reads the latest communities file and the latest reports file, matches reports by `member_hash`, and refuses (exit 2, nothing written) when a reportable community has no matching report or the communities file was made from a different builder output (compared by a hash of the community graph's input). It writes Community nodes, `IN_COMMUNITY` and `PARENT_OF` in the same transaction as the rest. Communities without a report are written with an empty report and never selected. It upgrades version 4 and refuses a newer version. The load report gains community counts.

**Validation.** `validate_graph()` adds: members are extracted non-MetricValue nodes; each lower Community has exactly one `PARENT_OF` parent; each top Community has at least one child; every `report_chunk_ids` entry is an evidence chunk of a member (top: of a member of a child); the recomputed member hash equals `member_hash`.

**Report embeddings.** The report text embedded is "title: summary". It goes through the existing cached node-embedding path (`retrieve.node_embed` gains reports; estimate first, `--embed` to send). The arm refuses uncached report texts at start.

**`graph_global` arm (ADR-0008).** Registered beside the other arms; implements the existing `Arm.run` contract and returns an `ArmResult` whose answer comes from the reduce step, not from `ChunkAnswerer`. Steps:
1. Question filter (ADR-0001) and the cached query embedding.
2. `community_select`: exact cosine between the query and every lower report with a report; keep, in rank order, the top N whose communities have evidence chunks in the filtered filings. Ties by `community_id`.
3. Per community, its evidence chunks inside the filter, ranked by cosine between the query and the chunk embeddings in `chunk_embeddings`, top 6.
4. `global_map`: one call per community, 4 at a time, `prompts/global_map/v1.md`, structured output `{points: [{text, score}]}` with `score` 0–100. A point citing a chunk outside its call's 6 is dropped and counted; a point with no citation is dropped.
5. `global_reduce`: points with score > 0, sorted by score then community rank, added until about 8k tokens; `prompts/global_reduce/v1.md` writes `answer/v1`'s status line and cited sentences. No point above 0: answer `not_found`, no call.
6. The existing citation filter and status handling, with the allowed set being every chunk shown to any map call for this question.

Map and reduce use claude-sonnet-5-5 at effort `high` through the response cache. The ranked list (`retrieved`) is the shown chunks, by community rank then cosine, deduplicated, scored for recall at 5 and 10 as other arms. `sources` (for judges) are the shown chunks. The per-question record holds the selected communities with similarity, the shown chunks per community, every map point with score and kept or dropped, the reduce input size, and stage costs. Config pins N, chunks per call (6), reduce budget, models, efforts and the three prompt versions; the run header records them with the communities and reports run names and the ontology version. The arm refuses to start unless `:GraphMeta` matches ontology version 5 and the schema hash.

**N tuning.** `python -m eval.global_n` scores retrieval only, from cached embeddings, at N ∈ {4, 6, 8, 12} on the 82 answerable dev questions, picks N by the 10 global questions' recall@10 (ties to the smaller), and writes `benchmarks/graph/global-n-run1.json` with every N's per-class scores and a top-level ablation (the same ranking over top reports). N is frozen in the arm config before the full run.

**Runs and spend.** Step 12 is capped at $6 in all: reports, embeddings, map and reduce calls, and judges. `eval.run` has no class selection today, so it gains a `--classes` option (harness change, approved by the user on 2026-10-15) and the full run covers only the 10 global, 6 decline and 5 unanswerable dev questions; the header records the selection. Estimate before the run. One full run, committed as `benchmarks/runs/{date}-graph_global-dev-{commit}.json`, then `eval.compare` against the vector baseline.

**Provenance check.** `python -m graph.global_provenance <run file>` reads a `graph_global` run, and for every kept sentence's citations finds a selected community whose members include a node with `EVIDENCED_BY` to that chunk, or an extracted edge among its members with the chunk in `chunk_ids`. Exit 0 no breaks, 2 bad arguments, 3 could not run, 4 breaks found; it writes `benchmarks/graph/global-provenance-run{N}.json` with every trace.

**Stop condition reading.** On the 10 dev global questions: `graph_global` recall@10 with an interval excluding 0, and zero provenance breaks. The paired comparison with `vector` (recall@5, recall@10, wrong evidence, judged relevancy, faithfulness, citation support) and H4 are reported whatever they show, labelled agent-drafted, tuned on dev and directional. H5 cost per query is reported; latency is reported only for live, uncached stages.

**Docs.** RUNBOOK Step 12 outcome, BUILD-LOG entry per ticket, README section with the communities and the global result, CLAUDE.md Decisions for choices made during tickets, and Commands for the new CLIs. `graph/` is already in git_state()'s dirty list.

## Testing Decisions

A good test checks behaviour through the highest seam a caller uses, on real data: real chunk IDs and spans from the fixture filings, recorded model replies, and the `neo4j-test` and Postgres throwaway schemas. It never asserts on prompt wording, log text or internal helper calls. Deterministic logic is shown failing before the code is written.

Seams (proposed; the fewer the better):
1. **`communities` module, pure.** Builder output in, communities out. Tests: metric values and structural nodes excluded; co-occurrence weights equal shared chunk counts; extracted edge weight equals evidence count; same input and seed give identical communities; a top community over 25 members gets lower communities and one under gets itself; member hashes stable under input order; singletons reported. Built from a small hand-made graph around real fixture chunk IDs, plus one run over the full builder output from the fixtures.
2. **Report writer, through the response cache.** Recorded Haiku replies for 2–3 real communities (captured by a script after its estimate is shown). Tests: request shape (model, effort, schema, pinned prompt hash); citation check drops uncited and out-of-community sentences and counts them; top reports may cite only kept lower citations; estimate and cap refusals.
3. **Graph load and `validate_graph()`, on `neo4j-test`.** Prior art: `tests/test_graph_load.py` and `tests/test_graph_validate.py` (one break per case). Tests: Community nodes and edges written in the rebuild transaction; refusal on a missing or stale report; version 4 upgraded, newer refused; each validator rule broken once.
4. **`graph_global` through `Arm.run`, on `neo4j-test` and Postgres.** Prior art: `tests/test_graph_traversal_arm.py`. Recorded Sonnet map and reduce replies for 2–3 real global questions. Tests: question filter limits communities and chunks; ranking and ties; a map point citing an unshown chunk dropped; reduce input sorted and capped; no point above 0 gives `not_found` with no reduce call; decline status; citation filter against the shown set; stage costs and spans (Cypher-style text-capture rule); start-up refusals (ontology, uncached reports); an API failure as `ArmError`.
5. **CLIs** (`graph.communities`, `graph.reports`, `eval.global_n`, `graph.global_provenance`): exit codes, files written, estimate-only default, refusals. The provenance check is tested on a run file with one deliberately broken citation.

Ticket 01's race test runs the parse from several threads on a recorded reply.

## Out of Scope

- `test` split runs, the router and `benchmarks/results.md` (Step 13).
- Full `graph_global` runs on lookup, local and multi-hop questions (Step 13's matrix and budget).
- Ablations listed in OPEN-DECISIONS: chunk handoff to `answer/v1`, Haiku map calls, LLM-rated community selection.
- Tuning community settings, edge weights or the report prompt against eval questions.
- Path-level citations and new answer prompts for the other graph arms (ADR-0007, Step 13).
- Re-extraction, entity resolution changes and new tickers or data sources.
- Extending the quality gate to graph arms.

## Further Notes

- Measured during the grilling (load run 4, `graspologic-native` 1.3.1, through `uv run --with`, not installed): 2,181 nodes, 1,224 extracted-edge pairs, 17,459 co-occurrence pairs; 34 top communities (largest 227), 111 lower (median 16, largest 63); the TSMC community holds 114 members from all three filers. Two runs gave identical output. 1.2.5 gave different clusters for the same seed, which is why the pin is exact.
- Cost estimates at the 2026-10-10 price table: reports about $0.30; global search about $0.20 per question at N = 8; 21 full-run questions about $4.20 plus judges. Ticket estimates replace these.
- Expected result: the research note's sources predict community search trails vector RAG on per-entity detail questions. Report it as found.
- The research note recommended ADR-0007's chunk handoff; the user chose map-reduce (Q1). The note's reasoning is kept in OPEN-DECISIONS as an ablation.
