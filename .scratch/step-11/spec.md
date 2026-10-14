# Step 11: Graph retrieval arms

Status: ready-for-agent
Sources: RUNBOOK Step 11 and the Step 10 outcome (q0127), docs/GRAPH-LAYER.md (Retrieval, text-to-Cypher hardening), docs/RESEARCH-PLAN.md (H1, H2, H5), docs/OPEN-DECISIONS.md (Step 11 and After Step 10 sections as they stood before this grilling), ADR-0001, ADR-0002, ADR-0004 (amendment of 2026-10-14, Step 11), ADR-0007, and the grilling session of 2026-10-14. Every round (Q1–Q30, the seams) was answered "as recommended", and the user moved to the spec. External research: docs/research/text-to-cypher-and-local-search.md (EXPLAIN query types, READ mode, timeouts and memory limits, row caps, schema warnings, Text2Cypher practice, GraphRAG local search, Neo4j indexes) and docs/research/neo4j-driver-and-constraints.md. Vocabulary: CONTEXT.md (arm, control arm, question filter, anchor, fallback, Cypher validity rate, risk factor body, evidence, persistence link).

## Problem Statement

Step 10 loaded the whole graph with zero violations. But the benchmark still has only one arm, `vector`, so the project cannot yet say whether the graph helps on any question. RESEARCH-PLAN's H2 (traversal beats vector on multi-hop recall@10 by 0.10) has nothing to test against. Vector's dev multi-hop recall@10 is 0.717, the weakest class, and it is where a graph should help if it helps anywhere.

Three things stand in the way:
- **Risk factor evidence stops at the heading.** Step 10's multi-hop check failed q0127. The graph linked NVIDIA's demand risk factor correctly from fiscal 2025 to fiscal 2026, but the list of causes the question asks about sits in the chunk after the heading, and no node or edge cites that chunk. Any graph arm built now would inherit the gap.
- **No arm reads the graph.** The harness knows only `vector`. Nothing links a question to graph nodes, collects their evidence or ranks it into the chunk list the answer model and the metrics expect.
- **Generated Cypher is unsafe on this database.** Neo4j Community has no read-only users. `EXPLAIN` marks `LOAD CSV` and `TERMINATE TRANSACTIONS` as read queries, and both run inside a READ transaction. Unknown labels raise only warnings, and a reversed edge raises nothing. Running model-written Cypher needs guards in our own code.

## Solution

First, give each risk factor evidence for its whole body. A deterministic builder step adds `EVIDENCED_BY` edges from each risk factor to every chunk its body touches. The same rebuild moves the graph to ontology version 4, which also lets Company be a `CONCERNS` end. The Step 10 multi-hop check is rerun. If q0127 still fails, the step stops and reports.

Then build two arms that end the way `vector` does: at most 10 ranked chunk IDs go to the unchanged answer prompt, so every difference against the control arm comes from retrieval alone (ADR-0007).
- **`graph_local`** links the question to anchors by exact embedding similarity plus name matches. It collects the anchors' evidence chunks and their edges' chunks, and ranks them by anchor similarity and how many of the anchor's edges cite each chunk.
- **`graph_traversal`** asks Claude to write Cypher from the generated ontology schema. The query must return a `chunk_ids` column. It passes five guards, gets one repair on failure, and falls back to `graph_local`. Every fallback is logged.

The Cypher validity rate is tracked from the first run as four rates per class. `eval.compare` gains paired comparisons against `vector`. Both arms run every `dev` record end to end, and the two full runs and the validity report are committed. `test` stays untouched until Step 13.

## User Stories

1. As the project owner, I want each risk factor's evidence to cover its whole body, so that a question about a factor's content reaches the chunk that holds it.
2. As the project owner, I want the body to start after the heading's last appearance in the risk-factor section, so that the "Risk Factors Summary" list does not count as a body.
3. As the project owner, I want the body to end at the next risk factor heading or the end of the section, so that one factor's evidence does not run into the next section.
4. As the project owner, I want every body longer than 5 chunks listed in the load report, so that I can see where a missed heading made a body run long.
5. As the project owner, I want body edges marked `body/v1` with confidence `stated`, so that built evidence is never mistaken for extracted evidence.
6. As the project owner, I want each body edge's span to be text from its own chunk, so that the existing span and evidence checks still hold.
7. As the project owner, I want ontology version 4 to allow Company as a `CONCERNS` end, so that the 5 agreement edges dropped at Step 10's load come back.
8. As the project owner, I want `graph.load --rebuild` to upgrade a graph recorded under an older ontology version in the same transaction, so that no one wipes a Neo4j volume by hand.
9. As the project owner, I want the loader to refuse a graph recorded under a newer or unknown ontology version, so that old code never overwrites a newer graph.
10. As the project owner, I want the load report and BUILD-LOG to say the loaded evidence came from `extract/v2@34a82e2b` under ontology 3's prompt, so that the provenance stays honest without re-extraction.
11. As the project owner, I want `graph.multihop` rerun after the rebuild, with the question and the pass rule unchanged, so that I know whether q0127's gap is closed before any arm depends on it.
12. As the project owner, I want the step to stop and report if q0127 still fails, so that no arm is built on a graph that cannot answer it.
13. As a benchmark reader, I want `graph_local` and `graph_traversal` to return at most 10 ranked chunk IDs to the same answer prompt as `vector`, so that any difference between arms comes from retrieval.
14. As a benchmark reader, I want both graph arms to apply the same question filter as `vector`, parsed from the question text only, so that period confusion is measured the same way for every arm.
15. As a benchmark reader, I want a graph arm that finds nothing to return no chunks rather than fall back to `vector`, so that graph results never contain control-arm retrievals.
16. As a benchmark reader, I want the no-anchor and not-found counts reported per class, so that an arm's good unanswerable score can be read for what it is.
17. As the project owner, I want `graph_local` to rank anchors by exact cosine between the question's query embedding and cached "Label: name" embeddings, so that search stays exact as ADR-0002 requires and no new index is needed.
18. As the project owner, I want risk factors embedded by title and metric values by concept and period, so that lookup and risk questions can anchor on them.
19. As the project owner, I want nodes whose name or alias RapidFuzz-matches a phrase in the question ranked first among anchors, so that a named entity always beats a merely similar one.
20. As the project owner, I want the three filers never used as anchors, so that a question naming NVIDIA does not pull in everything NVIDIA connects to.
21. As the project owner, I want anchors to need evidence in the filtered filings, so that a period filter is not undone by anchoring on another year's node.
22. As the project owner, I want the 20 nearest nodes taken as anchors, as GraphRAG does, so that the arm has the same starting breadth as a documented design.
23. As the project owner, I want each chunk scored as the sum over anchors of similarity × (1 + the number of that anchor's edges citing the chunk), with ties broken by chunk ID, so that the ranking is deterministic and explainable.
24. As the project owner, I want the persistence links counted as edges during expansion, so that period questions reach the other year's risk factor.
25. As the project owner, I want 1 hop against 2 hops chosen on dev retrieval-only scores and frozen before the full run, so that the choice costs nothing and is recorded.
26. As a benchmark reader, I want the report to say the hop choice was made on dev, so that I know dev numbers for this arm are tuned on.
27. As the project owner, I want `graph_traversal` to use claude-sonnet-5-5 at effort `high` with structured output `{reasoning, cypher}`, so that validity is not traded for cost and the reasoning is kept.
28. As the project owner, I want the Cypher prompt in a versioned file pinned by SHA-256, so that a score change can be attributed to a prompt version.
29. As the project owner, I want the prompt to hold the generated ontology schema text, the 24 filings' ticker, form, fiscal period and accession number, and the parsed question filter as a hint, so that the model writes labels and literals that exist.
30. As a benchmark reader, I want the prompt's few-shot examples to use invented questions, never eval questions, so that no scored question echoes its prompt.
31. As the project owner, I want the traversal arm to refuse to start unless the graph's recorded ontology version and schema hash match the code, so that the prompt always describes the live graph.
32. As the project owner, I want every generated query to return a `chunk_ids` column, so that its evidence is explicit and ranking it is simple.
33. As the project owner, I want the executor to flatten `chunk_ids` in row order, drop duplicates and chunks outside the question filter, and cut to 10, so that traversal output fits the arm contract.
34. As the project owner, I want text rules that reject more than one statement, `LOAD CSV`, `TERMINATE`, `SHOW`, `USE`, every write clause and every procedure `CALL` (subqueries allowed), so that the reads `EXPLAIN` misreports never run.
35. As the project owner, I want our own check that every label, edge type and property in a query exists in the ontology, and that every pattern with labelled ends uses an allowed endpoint pair in its direction, so that what Neo4j only warns about, or ignores, fails.
36. As the project owner, I want `EXPLAIN` to report query type `r` with no unknown-name notification before execution, so that the database's own planner agrees the query only reads.
37. As the project owner, I want execution in a READ transaction with a 10 s driver timeout, no automatic retry and at most 1,000 rows read, so that one bad query cannot stall a run.
38. As the project owner, I want both Neo4j services in compose to set a 15 s transaction timeout and a 512 MB transaction memory limit, so that the server stops a runaway query even after the client stops reading.
39. As the project owner, I want one repair attempt with the error message when a query fails a guard, errors, times out or returns no chunk IDs, so that a misspelled literal gets a second chance.
40. As the project owner, I want a query that still fails after its repair to fall back to `graph_local`, with the reason logged as `invalid`, `error`, `timeout` or `empty`, so that every fallback is visible and counted.
41. As a benchmark reader, I want the Cypher validity rate reported per class as four rates (first-pass valid, valid after repair, non-empty, fallback), so that the real number is published, not a flattering one.
42. As the project owner, I want at most one prompt revision (`cypher/v2`), driven by failure categories rather than single questions, with both versions' validity reported, so that tuning on dev stays small and visible.
43. As the project owner, I want each traversal run's validity rates and every generated query committed, so that anyone can reread what the model wrote.
44. As the project owner, I want per-question cost and latency split into anchor linking, Cypher generation, repair and execution stages, so that H5 (graphs cost more) can be checked stage by stage.
45. As the project owner, I want the run header to record the Cypher prompt version and hash, the ontology version, the schema hash and the graph load run, so that a run can be traced to the graph it read.
46. As a benchmark reader, I want `eval.compare` to report fixed-seed paired bootstrap differences against `vector` per class, for retrieval metrics and the judged metrics marked "uncalibrated", so that a difference counts only when its 95% interval excludes 0.
47. As the project owner, I want all `uncertain` evidence kept, with each retrieved chunk's best confidence in the per-question record, so that a filter ablation can be judged later from data.
48. As the project owner, I want trace spans for anchor linking, graph expansion, Cypher generation, the guard, execution and fallback, with the generated Cypher always on its span, so that I can read a failed question in Phoenix.
49. As the project owner, I want prompt text on spans only under `FILINGINTEL_TRACE_TEXT`, so that the Step 6 rule still holds.
50. As the project owner, I want retrieval-only iteration before any full run, so that tuning costs close to nothing.
51. As the project owner, I want one full dev run per arm, answers and judges included, committed as the Step 11 results, so that the stop condition is met with real numbers.
52. As the project owner, I want Step 11's total spend capped at $15, with the estimate shown before each paid run, so that costs stay predictable.
53. As the project owner, I want the quality gate left vector-only, so that CI does not need Neo4j snapshots before Step 13.
54. As a benchmark reader, I want every result labelled "agent-drafted questions", so that the provenance rule from Step 4 holds.

## Implementation Decisions

**Body evidence and ontology version 4 (ticket 1)**
- The pure graph builder gains a body step. Its inputs are each filing's sections with offsets, its chunks with offsets, and the RiskFactor nodes with their evidence spans. For each risk factor it finds where the heading's evidence span appears last inside the risk-factor section: Item 1A for a 10-K, Part II Item 1A for a 10-Q. Spans are matched with the Step 8 normalization. The body runs from the end of that heading to the start of the next risk factor heading found the same way, or to the end of the section. Every chunk the body touches gets an `EVIDENCED_BY` entry with `extract_prompt` `body/v1`, `confidence` `stated` and `evidence_span` set to the first 200 characters of the body text inside that chunk, taken verbatim from the chunk text.
- The heading chunk keeps its extracted entry. The usual combining rule (one entry per chunk, most confident wins) settles any overlap.
- A risk factor whose heading cannot be located in the section gets no body entries and is counted in the build report. Bodies longer than 5 chunks are listed. There is no cap.
- The builder's prompt-mix check covers extract files only. `body/v1` entries, like `persist/v1` links, are added by the builder and are not extract prompts.
- `ONTOLOGY_VERSION` becomes 4. `CONCERNS` allows Company as an end. The schema text and its hash change, which also changes the extract prompt's computed hash. No extraction is rerun: the loader keeps matching the `extract/v2@` prefix. The load report records the ontology version the evidence was extracted under.
- `graph.load --rebuild` accepts a `:GraphMeta` with a lower ontology version. It re-applies constraints and rewrites `:GraphMeta` in the rebuild transaction, and records the upgrade in the load report. A higher or unparseable version is refused with the existing exit code. The `neo4j-test` path behaves the same.
- After the rebuild (load run 4), `graph.multihop` runs unchanged. A q0127 failure stops the step for the user.

**Harness seams (ticket 2)**
- `retrieve/arms.py` gains the two arms. Each graph arm's spec lists its required env (Neo4j settings, `DATABASE_URL`, `VOYAGE_API_KEY`, and `ANTHROPIC_API_KEY` for traversal and the answer model).
- `Retrieval` gains optional graph fields. For `graph_local`: anchors (key, label, similarity, how matched), hop count, no-anchor flag. For `graph_traversal`: the Cypher attempts with their outcomes, the fallback reason and validity flags. Vector runs are unchanged, and `eval.gate` reads nothing new.
- Operational records gain stages: anchor linking, Cypher generation, repair and execution, each with latency, tokens and cost. Cypher calls are priced from the existing dated price table. A model with no price still refuses to start.
- The run header adds the Cypher prompt version and hash, ontology version, schema hash and the graph load-run ID, read from `:GraphMeta` and the latest load report.
- `eval.compare` adds paired comparisons. For questions both runs hold, per class, it runs a fixed-seed paired bootstrap of the difference in recall@5, recall@10, precision@5, precision@10 and wrong-evidence rate, and of the judged means labelled "uncalibrated". It reports the mean difference and its 95% interval and marks those whose interval excludes 0. It keeps refusing runs with different answer prompts.
- Per-question records gain each retrieved chunk's best evidence confidence (graph arms only).

**`graph_local` (ticket 3)**
- Node embeddings are "Label: name" for the cross-filing labels, "RiskFactor: title" and "MetricValue: concept, period". They are embedded once with Voyage (`document` input type) through the existing entity-embedding cache, and stored on disk, not in Neo4j. The question embedding is the vector arm's cached query embedding.
- Anchor ranking:
  1. Nodes whose name or an alias matches a phrase of the question under RapidFuzz (cutoff chosen in the ticket and recorded) come first, by match score.
  2. Then the rest by exact cosine, down to 20 anchors.
  3. Company nodes are excluded. A node qualifies only if at least one of its evidence chunks lies in the filtered filings, or in any filing when there is no filter.
- Expansion reads Neo4j in a READ transaction. At 1 hop, it takes the anchor's `EVIDENCED_BY` chunks and the `chunk_ids` of every edge touching the anchor, `PERSISTS_AS` included. At 2 hops, it also takes those neighbours' `EVIDENCED_BY` chunks.
- A chunk's score is the sum over anchors of similarity × (1 + the number of that anchor's edges citing the chunk). Name-matched anchors use similarity 1.0. Chunks outside the filter are dropped, ties break by chunk ID, and the list is cut to 10.
- Hop count is a config value. Both are scored retrieval-only on dev, the higher pooled recall@10 is frozen (ties go to 1 hop), and the choice and both scores go to BUILD-LOG.
- No anchor, or no chunk left after the filter, means an empty retrieval, and the answer model then answers `not_found`.

**Cypher guard and executor (ticket 4)**
- A new module in the graph package takes a query string and returns a guard result: a pass, or the first failed rule with a message. The checks run in order:
  1. text rules (single statement after stripping comments and string literals; the deny list in ADR-0007, procedure calls detected as `CALL` not followed by `{` or `(`);
  2. the ontology check (every `:Label`, `[:TYPE]` and `.property` token must exist; patterns with labels on both ends must be allowed endpoints in the written direction, undirected patterns accepted when either direction is allowed, unlabelled ends not checked);
  3. `EXPLAIN` in a READ session, which must report query type `r` with no 01N50, 01N51 or 01N52 notification;
  4. execution in a READ transaction with a 10 s timeout, read through an explicit transaction so the driver does not retry transient errors, stopping after 1,000 rows. The result must have a `chunk_ids` column holding lists of strings.
- Errors and timeouts become guard results with their category, never exceptions to the arm.
- `docker-compose.yml` sets `NEO4J_db_transaction_timeout=15s` and `NEO4J_db_memory_transaction_max=512m` on `neo4j` and `neo4j-test`. The CI service gets the same settings.
- The `graph.multihop` read helper and the executor share one read-session function.

**`graph_traversal` (ticket 5)**
- `prompts/cypher/v1.md` holds a system part and a user template. The system part has `$schema` (the generated `schema_text()`), `$filings` (a table of the 24 filings), the rules (one read query; return a `chunk_ids` list collected from `EVIDENCED_BY` chunks and edge `chunk_ids`; use only listed labels, types and properties; no LIMIT needed) and 4–5 invented worked examples (one hop, persistence across years, a metric value, two hops, a peer comparison). The user template has the question and the parsed filter. The file is pinned by SHA-256, and the version string is `cypher/v1@<hash8>` over the rendered system prompt, the template and the output schema, as `extract/` does.
- The call is claude-sonnet-5-5 at effort `high`, structured output `{reasoning: string, cypher: string}`, through the response cache.
- The repair call sends the original messages plus the failed query and the guard's message, once.
- The executor result becomes the ranked chunk list (row order, first appearance, dedup, filter, cut to 10).
- After a second failure, or a second empty result, the arm runs `graph_local` on the same question and records the fallback reason. Its retrieval fields keep both attempts.
- The arm checks `:GraphMeta` against the code at start.
- `benchmarks/graph/cypher-validity-run{N}.json` holds, per traversal run: commit, prompt version, model and effort, per-class counts and the four rates, and per question the attempts (query, rule or error, rows) and the fallback reason.
- A `cypher/v2` revision is allowed once, written from failure categories. Both versions' validity files are kept.

**Runs and closure (ticket 6)**
- Retrieval-only iteration first. Then one full dev run per arm with answers and 3 judge passes, the estimate shown first and the $15 total cap checked against committed spend. Both run files are committed, along with the validity report.
- `eval.compare` runs each graph arm against the baseline vector run. The results go to BUILD-LOG with commits, labelled agent-drafted, judged numbers uncalibrated, dev tuned-on.
- The RUNBOOK Step 11 outcome and README rows follow.

## Testing Decisions

- Tests check behaviour at the seams agreed in the grilling, through public interfaces only. They never assert on internal helpers, query text built inside an arm, or call order.
- Deterministic logic is written test-first, with each test shown failing before the code (`/tdd`): body boundaries, anchor ranking, chunk scoring, the guard's text and ontology rules, flattening `chunk_ids`, paired bootstrap.
- Seams:
  1. **The graph builder** (prior art: `tests/test_graph_builder.py`). Body evidence on the real gzipped fixture filings and their chunks: a body that ends at the next heading, one that ends at the section end, the summary list skipped, a heading that can't be located (counted, no entries), a long body flagged, `body/v1` entries alongside extracted ones combined by the usual rule.
  2. **`graph.load --rebuild`** (prior art: `tests/test_graph_load.py`). On `neo4j-test`: an older `:GraphMeta` upgraded and reported; a newer one refused with nothing changed; `CONCERNS` to Company accepted under v4.
  3. **`Arm.run(question)`** for both graph arms (prior art: `tests/test_vector_arm.py`, `tests/vector_fakes.py`). On `neo4j-test`, a small hand-built graph around real fixture chunk IDs and real spans; fake embedders and recorded model replies. Cases:
     - ranking order and tie-breaking;
     - a name match beating a closer embedding;
     - filers never anchored;
     - the question filter dropping another period's chunks;
     - no anchor giving an empty retrieval;
     - traversal's happy path;
     - a guard failure repaired;
     - an empty result repaired, then fallen back;
     - every fallback reason recorded;
     - a `:GraphMeta` mismatch refusing to start.
  4. **The Cypher guard** (new seam; prior art: the one-break-per-case style of `tests/test_graph_validate.py`). On `neo4j-test`, each bad query breaks exactly one rule and the result names it:
     - `LOAD CSV`, `TERMINATE TRANSACTIONS`, `SHOW`, a write clause, a procedure `CALL`, two statements;
     - an unknown label, type or property; a reversed edge;
     - a missing `chunk_ids` column;
     - a query that exceeds the timeout.
     A valid query passes and returns its rows. A test proves a write cannot reach the database even if text rules were bypassed (READ mode refuses it).
  5. **`eval.compare`** (prior art: `tests/test_eval_compare.py`): paired differences on two small run files, fixed seed, intervals reproducible, a zero-difference case excluded from "counts".
  6. **`eval.run`** (prior art: `tests/test_eval_run.py`, `tests/test_tracing_run.py`): the new header fields and cost stages are written, and graph spans carry the Cypher but no prompt text unless `FILINGINTEL_TRACE_TEXT=true`.
- CI stays offline. Neo4j tests skip without `NEO4J_TEST_URI`, as now, and the existing guard test keeps CI from skipping them silently. Recorded replies for the Cypher prompt are captured by a script, as the extract ones were.
- Verification per ticket: `uv run ruff check .`, `uv run --env-file .env pytest`, and the relevant slice: `graph.multihop` for ticket 1, retrieval-only dev runs for tickets 3 and 5.

## Out of Scope

- `graph_global`, communities and community summaries (Step 12).
- The router, `benchmarks/results.md`, the full four-arm matrix and scoring `test` (Step 13).
- Graph paths in the answer context, path-level citations and the path precision metric (Step 13, ADR-0007).
- Extending the quality gate to graph arms (Step 13).
- A Haiku Cypher generator and an `uncertain`-evidence filter, as ablations (OPEN-DECISIONS).
- Re-extraction under the v4 schema, persistence-link changes (several predecessors) and entity-resolution changes.
- Neo4j full-text or vector indexes, LangGraph, neo4j-graphrag, CyVer, APOC or any new dependency.
- Judge calibration (still required before the final benchmark).

## Further Notes

- Every number is labelled "agent-drafted questions". Judged metrics are "uncalibrated". Dev numbers for graph arms are tuned-on (hop choice, at most one prompt revision).
- Vector's dev baseline for comparison: recall@10 lookup 1.000, local 0.977, multi_hop 0.717, global 0.800; one full run costs about $3.50. `graph_local` has little headroom on lookup and local questions. H1 predicts vector holds there, and a graph loss is a result to report, not to fix.
- The 3 Step 10 multi-hop questions (q0111, q0120, q0127) are dev questions scored like any other. Their hand-written Cypher must not be shown to the generator.
- Cost estimate: Cypher generation and repairs about $1–2 over the dev set; two full runs about $7; node embeddings a few cents. That is within the $15 cap.
