# Step 10: Load the graph

Status: ready-for-agent
Sources: RUNBOOK Step 10, docs/OPEN-DECISIONS.md (Step 10 section), ADR-0004, ADR-0006, the Step 8 rewrite rule in CLAUDE.md Decisions, and the grilling session of 2026-10-13 (rounds 1–3, all answered "as recommended"; round 3 taken as accepted when the user moved to the spec). External research: docs/research/neo4j-bulk-load.md (UNWIND, transactions, MERGE under constraints, Eager, memory limits on Neo4j 5.26 Community with driver 6.3.1) and docs/research/neo4j-driver-and-constraints.md. Vocabulary: CONTEXT.md (metric value, unit class, persistence link, canonical key, alias map).

## Problem Statement

Steps 7–9b built the ontology, the guarded write path, extraction over all 24 filings and entity resolution, but nothing is in Neo4j. The graph arms in Step 11 need a loaded graph, and the RUNBOOK's stop condition asks whether the graph can answer multi-hop questions at all. If it can't, the ontology is wrong, and that is cheaper to learn now.

Four things stand in the way:
- **Metric values collide.** One key holds both a percentage and a dollar figure when they share a concept name (NVIDIA's "net income" as 55.6 percent and as $120,067 million), and the same figure at two scales ($5.5 billion against $5,473 million) is counted as a conflict. 301 of the corpus's 332 extraction conflicts are on metric values.
- **The write path appends.** `write_batch()` appends evidence, keeps the first `EVIDENCED_BY` span and lets node properties take the latest batch's values. These are Step 7's rules; Step 8 replaced them with "a chunk's entry is replaced, the most confident evidence wins". It also uses `SET +=`, which plans an Eager step and deletes any property whose value is null, and it does not catch the same edge twice in one batch.
- **Resolution is not applied.** Entity resolution wrote an alias map (ADR-0006); nothing turns many extracted keys into one canonical node with its evidence combined.
- **Risk factors are not linked across periods.** RiskFactor is one node per filing (ADR-0004). Without persistence links, "how did this risk factor change" cannot be answered from the graph.

## Solution

A loader rebuilds the whole graph from committed and local inputs in one transaction: structural nodes from Postgres, extracted records from the extract files, the alias map, and persistence links. Everything is computed in Python first. Each node and edge exists once with its final properties and evidence, and is then written through `write_batch()`, which keeps every check. A rerun gives the same graph.

Before loading, extraction is replayed from the response cache ($0) with a new metric-value key that includes the unit class, and with values at different scales compared after conversion. Resolution is then rerun so the alias map's metric-value entries match the new keys; every other label's alias entries must be unchanged.

Persistence links join each risk factor in a 10-K to the same risk in the company's previous 10-K, and each risk factor in a 10-Q to the same risk in the most recent 10-K before it. Identical normalized titles link with no LLM call; the rest go through Voyage top-3 candidates and a Haiku adjudicator, about $1. A labeller scores the links once (agent-labelled, about $0.10).

After the load, `validate_graph()` runs, and three agent-drafted dev multi-hop questions are answered with hand-written Cypher, each checked against the question's gold chunks.

## User Stories

1. As the project owner, I want the whole graph rebuilt from inputs in one transaction, so that a rerun gives the same graph and a failure leaves the previous graph untouched.
2. As the project owner, I want the loader to refuse a non-empty graph unless I pass `--rebuild`, so that I never wipe a graph by accident.
3. As the project owner, I want the rebuild to keep `:GraphMeta` and the constraints, so that the ontology version check still guards every write.
4. As the project owner, I want every node and edge to pass through `write_batch()`, so that the ontology and evidence checks apply to the load exactly as to any other write.
5. As the project owner, I want Company, Filing, Period and Chunk nodes built from Postgres, so that structural nodes come from EDGAR metadata, not from text.
6. As the project owner, I want a Chunk node for every chunk in Postgres, cited or not, so that Step 11's local search can reach any chunk.
7. As the project owner, I want Chunk nodes to hold no text, so that text keeps coming from `resolve()` and the graph cannot drift from Postgres.
8. As the project owner, I want the loader to take, per filing, the latest extract run under `extract/v2` and refuse a mix of prompt versions, so that NVDA FY2026's v1 run never loads beside v2 runs.
9. As the project owner, I want each extracted key mapped through the alias map to its canonical key, so that one real-world entity is one node.
10. As the project owner, I want a merged node to take its canonical member's own properties and list the other members' names in `aliases`, sorted and without repeats, so that the graph keeps every printed name.
11. As the project owner, I want a merged edge's evidence to hold every member's entries ordered by filing date, then chunk, so that its evidence reads in time order.
12. As the project owner, I want one entry per chunk on a merged edge, keeping the more confident when a chunk appears twice and the later on a tie, so that the four evidence lists stay parallel and unique.
13. As the project owner, I want a single-valued edge property that differs across members to take the most confident value, ties to the later filing, so that Step 8's rule holds across filings.
14. As the project owner, I want every such disagreement counted in the load report with both values, so that conflicts stay visible.
15. As the project owner, I want one `EVIDENCED_BY` edge per (node, chunk), with the more confident entry winning when two members cite the same chunk, so that node evidence follows the same rule as edge evidence.
16. As the project owner, I want a metric value's key to include its unit class, so that a percentage and an amount with the same concept name become two metric values.
17. As the project owner, I want a hand-written table mapping every printed unit string to a unit class and scale, so that "USD millions" and "USD billions" are one class at two scales.
18. As the project owner, I want an unknown unit string to fail the replay with its name, so that a new unit is mapped on purpose, never guessed.
19. As the project owner, I want values in one key compared after conversion to one scale, with two values that agree within the rounding of the less precise one kept as the more precise one and not counted as a conflict, so that "$5.5 billion" and "$5,473 million" no longer show up as a disagreement.
20. As the project owner, I want the unit-class change made in code only, with the ontology at version 3, so that the extract prompt's hash is unchanged and the replay costs $0.
21. As the project owner, I want the replay to refuse to run if any chunk would need a new API call, so that a key change can never start paid extraction.
22. As the project owner, I want resolution rerun after the replay and the alias map for every label except MetricValue checked byte-identical to the committed one, so that scoring round 2 still describes the loaded map.
23. As the project owner, I want the two Intel metric values that lost their MEASURES edge left out and listed in the load report, so that no edge loads without evidence.
24. As the project owner, I want Intel chunk 0000050863-24-000076:0017 recorded as a gap in the load report and the README, so that readers know one chunk was never extracted.
25. As the project owner, I want persistence links from each 10-K to the company's previous 10-K, and from each 10-Q to the most recent 10-K before it, so that annual changes are one hop and each quarterly update links to the list it amends.
26. As the project owner, I want risk factors whose normalized titles are identical within a linked filing pair linked with no LLM call, so that the 319 obvious links cost nothing.
27. As the project owner, I want the other risk factors' top-3 Voyage neighbours in the earlier 10-K sent to an adjudicator, so that reworded factors can still link.
28. As the project owner, I want the adjudicator (Haiku 5.5, effort high, prompt `persist/v1`) to answer `same`, `distinct` or `unsure` with a verified quote from each side, so that only a checked `same` links.
29. As the project owner, I want each risk factor to have at most one predecessor, the exact-title match or else the highest-ranked `same`, while an earlier factor may have several successors, so that a split factor links both halves without a later factor pointing two ways.
30. As the project owner, I want a pre-run cost estimate and a $2 cap on persistence adjudication, with replies cached, so that the cost is known and reruns are free.
31. As the project owner, I want each persistence link to carry two evidence entries, one chunk and span from each risk factor, so that invariant 4 holds.
32. As the project owner, I want the method visible on every link (`persist/exact_title` or `persist/v1@<hash8>` in the extract-prompt list), so that deterministic and adjudicated links can be counted apart.
33. As the project owner, I want persistence links scored once on 100 pairs (60 adjudicated, 25 near, 15 rest, fixed seed) labelled by gpt-6-luna, with precision and recall and bootstrap intervals, so that the links have a number like entity resolution does.
34. As a reader of the README, I want every persistence score labelled "agent-labelled (gpt-6-luna)", so that I know no human checked the labels.
35. As the project owner, I want the persistence prompt frozen before any label is seen, so that the score is not tuned on its own labels.
36. As the project owner, I want `write_batch()` to set properties by name instead of `SET +=`, so that the write plans no Eager step and a null value never deletes a property.
37. As the project owner, I want `write_batch()` to write the four evidence lists and each `EVIDENCED_BY` entry as given, so that the evidence rules live in one place, the Python build.
38. As the project owner, I want `check_batch()` to reject a batch holding the same node key or edge identity twice, so that one item can never silently overwrite another in a write.
39. As the project owner, I want `validate_graph()` run after the load, with its result in the load report, so that zero violations is recorded, not assumed.
40. As the project owner, I want a committed load report with commit, alias map hash, extract file hashes, ontology version, counts per label and edge type, conflicts, skipped items, the chunk gap, write time and the validation result, so that the load is reproducible and auditable.
41. As a reader of the README, I want a short table of the load report, so that the graph's size and gaps are visible without opening JSON.
42. As the project owner, I want three agent-drafted dev multi-hop questions answered with hand-written Cypher (TSMC reliance, competitors naming each other, NVIDIA's customer-investment risk factor across FY2025 and FY2026), so that I learn whether the ontology can answer multi-hop questions.
43. As the project owner, I want a question to pass only when its Cypher returns evidence chunk IDs including at least one gold chunk for each company or period it asks about, so that "the graph can answer it" has a fixed meaning.
44. As the project owner, I want each query, its output and its verdict in BUILD-LOG, so that the check can be rerun and read.
45. As the project owner, I want a failing question reported and treated as a reason to revisit the ontology, so that the stop condition is never passed by editing the question or the pass rule.
46. As the project owner, I want the loader to need no network, so that a rebuild is free and can run anywhere the inputs are.
47. As the project owner, I want every paid command (persistence adjudication, labelling) to check its API key, refuse when the provider's base URL is set, and show its estimate first, so that it follows the same rules as earlier steps.

## Implementation Decisions

- **Graph builder (new deep module).** One pure function takes the structural rows, the extract records, the alias map and the persistence links, and returns one write-path batch plus a build report (counts, conflicts, skipped items, gaps). All combining happens here: alias mapping, canonical properties and `aliases`, evidence ordering and de-duplication, the most-confident rule across filings, and `EVIDENCED_BY` selection. It makes no database or network call.
- **Loader command.** `python -m graph.load` (offline) reads Postgres for structural rows, the extract files, the committed alias map and the committed persistence links; calls the builder; checks the graph is empty or `--rebuild` was given; writes in one `execute_write` that deletes every node except `:GraphMeta` and writes the batch; runs `validate_graph()`; writes the load report under `benchmarks/graph/`. Exit codes follow `graph.validate`'s pattern (0 ok, 2 bad arguments, 3 could not run, 4 violations).
- **Write path.** `write_batch()` stays the only way in. It sets each property by name, writes evidence lists and `EVIDENCED_BY` entries as given (no append, no first-wins), and `check_batch()` gains a duplicate-item rule (same node key, or same start, type, end and role). The rebuild's delete runs in the same transaction, before the checks that read existing chunks and endpoints. Transaction memory stays at the server default; the report records write time.
- **Metric-value key.** The key becomes `{accession_no}:{subject key}:{norm(concept)}:{unit class}:{period}`, escaped as today. The unit table (printed unit string → unit class and scale) is hand-written in code and covers the 22 strings now in the corpus. Scale conversion applies only to comparing values inside the merge; the stored value and unit are the winning entry's as printed. The rounding rule: two values agree when they differ by no more than half a unit in the last printed digit of the less precise one, after conversion. The ontology, the extract prompt and its hash are unchanged.
- **Replay.** `extract.run` is rerun for all 24 filings with a flag that refuses any chunk not in the response cache. New run numbers are written; the old run files stay. Resolution is then rerun (`resolve.run`, cached adjudicator answers only), and a check compares the new alias map against the committed one for every label except MetricValue and stops on any difference.
- **Persistence linking (new module).** Same company only; filing pairs as in the solution (12 pairs). Candidates: exact normalized title (deterministic link); otherwise the top 3 Voyage neighbours among the earlier 10-K's risk factors, embedding "title plus span" through the batched, cached entity-embedding path. Adjudicator prompt `prompts/persist/v1.md`, structured output, response cache, quotes checked like Step 8 spans; `unsure` never links. Selection gives each later risk factor at most one predecessor. Output: a committed link file and run report under `benchmarks/persistence/`, with provenance, counts by method, decisions, tokens and cost. Cap $2, estimate first.
- **Persistence scoring.** Reuses the Step 9 draw, label and score machinery where it fits: a 60 / 25 / 15 draw with its own seed from the linked filing pairs, a label prompt for gpt-6-luna that never sees the adjudicator's answer, precision and recall on the selected links with fixed-seed bootstrap 95% intervals. Draw and labels are write-once committed files, scored once. A freeze file pins the persistence config, prompt and models before the draw.
- **Persistence link evidence.** `chunk_ids` holds one chunk from each risk factor (earlier first), `evidence_spans` the matching span (the risk factor's own span for an exact-title link, the adjudicator's verified quote otherwise), `confidences` `stated` for both, `extract_prompts` the method string.
- **Multi-hop check.** Three Cypher files committed under `benchmarks/graph/`, with a small runner that executes them read-only and checks the pass rule against the eval records' gold chunks. Results go to BUILD-LOG.
- **Decisions recorded.** CLAUDE.md Decisions gets a Step 10 entry (rebuild model, unit-class key, persistence rules, scoring, the two skipped metric values, the chunk gap). ADR-0004 gets a dated amendment: evidence is combined before the write under the most-confident rule, and the graph is rebuilt, never appended to. `git_state()`'s dirty list already covers `graph/`; the new persistence module's directory is added to it.
- **OPEN-DECISIONS.** Remove the settled Step 10 items; keep or add: ER recall misses, `MAX_CHUNKS_PER_NODE`, re-extracting Intel 0017 with streaming, incremental loads (replace-in-place in Cypher) for a larger corpus.

## Testing Decisions

- Good tests check behaviour at the module's interface: given inputs, the batch, report, key or links that come out. They never assert on Cypher text or private helpers.
- **Seams.** Three, each the highest point that covers its rules:
  1. The graph builder: plain records in, batch and report out. Most rules are tested here: alias mapping, `aliases`, evidence order and de-duplication, the most-confident rule across filings, `EVIDENCED_BY` selection, skipped metric values, the chunk gap.
  2. `write_batch()` against the `neo4j-test` database: named sets (a null no longer deletes a property), evidence written as given, the duplicate-item rule, and the rebuild's delete inside the same transaction. Prior art: tests/test_graph_write.py, tests/test_graph_batch.py and tests/graph_test_data.py.
  3. The persistence linker with a fake adjudicator and fake embeddings: candidate pairs, exact-title links, one-predecessor selection, evidence shape. Prior art: tests/test_resolve_candidates.py, tests/test_resolve_groups.py, tests/test_resolve_adjudicate_recorded.py (recorded real replies).
- The metric-value key and unit table are tested through the extract key and merge tests (prior art: tests/test_extract_keys.py, tests/test_extract_merge.py), using the real collisions: NVIDIA net income in percent and USD, AMD Q1 FY2025 net revenue in millions and billions.
- Fixtures use real chunk IDs and spans from the fixture filings, never hand-written HTML. Recorded real adjudicator and labeller replies are captured once for a handful of pairs.
- Deterministic logic is test-first: each test shown failing before the code.
- An end-to-end load test over the fixture filings writes into `neo4j-test` and runs `validate_graph()` with zero violations. It skips without NEO4J_TEST_URI, guarded in CI as today.
- The replay's cache-only flag is tested with a cache missing one chunk: the run refuses and names it.

## Out of Scope

- Changing entity resolution (adjudicator prompt, `unsure` handling, candidate rules, chunks per node). The misses stay in OPEN-DECISIONS.
- Re-extracting Intel chunk 0017, or streaming support.
- Any ontology change, including a `unit_class` property.
- Mapping metric concepts to XBRL tags.
- Incremental or per-filing loads, and deleting one filing from a live graph.
- Persistence links for filings whose previous 10-K is outside the corpus, and 10-Q-to-10-Q links.
- A tuning round for the persistence prompt.
- Retrieval arms, text-to-Cypher guards and making `validate_graph()` a CI gate (Steps 11 and 13).
- Changing transaction memory settings in docker-compose.yml.

## Further Notes

- Costs: replay $0; resolution rerun $0; persistence adjudication about $1 (cap $2); labelling about $0.10. Each paid command shows its estimate first.
- Facts from the grilling: 24 extract files, 8,084 nodes and 12,660 edges before resolution; 332 extraction conflicts, 301 on metric values; 767 risk factors have a linkable earlier 10-K, 319 by exact title; Step 9's Haiku adjudicator cost about $0.0008 a pair.
- Research findings that shaped this spec: one transaction with 10k node and 15k edge MERGEs took 5.5 s; `CALL { } IN TRANSACTIONS` cannot run in a managed transaction and commits in pieces; MERGE stays single under concurrency with the constraints; `SET +=` plans Eager and deletes on null; a memory-limit error is retried as transient for about 28 s.
- The local Neo4j's version-1 `:GraphMeta` was cleared on 2026-10-13; `apply_constraints()` creates the version-3 one before the first load.
- The three multi-hop questions are agent-drafted and labelled as such wherever their result is reported.
