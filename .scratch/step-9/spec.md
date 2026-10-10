# Step 9: Entity resolution

Status: ready-for-agent
Sources: RUNBOOK Step 9, docs/GRAPH-LAYER.md (Entity resolution), ADR-0004, ADR-0005, ADR-0006, docs/OPEN-DECISIONS.md, and the grilling session of 2026-10-10, whose decisions are in CLAUDE.md Decisions (Step 9 entries). External research: docs/research/entity-resolution.md covers evaluation design, blocking, clustering, libraries, legal suffixes, LLM matching, how other GraphRAG tools resolve entities, and SEC's XBRL API. docs/research/claude-extraction-structured-output.md still covers the Claude API.

## Problem Statement

Step 8 extracted one filing, NVIDIA's FY2026 10-K, into 389 checked nodes. The graph cannot be loaded yet, for two reasons.

First, the same thing appears under several names. Within that one filing, Groq is three Organization nodes ("Groq", "Groq, Inc", "Groq, Inc."), GDPR is two Regulation nodes, Timothy Teter is two Person nodes, and NVIDIA's FY2026 revenue is two MetricValue nodes ("Revenue", "Total revenue"). Across 24 filings from three companies, suppliers, customers, regulations and agreements will recur under many more names. A graph that keeps them apart cannot answer the multi-hop questions it exists for: "which suppliers do NVIDIA and AMD share" fails if TSMC is four nodes.

Second, the extraction itself is not finished. Only one filing is extracted, and it labels NVIDIA's market platforms (Data Center, Gaming, Professional Visualization, Automotive) as Segments, which the ontology reserves for reportable segments. Segment keys also ignore the filer, so two companies' "Gaming" segments would share one node before any resolution runs.

The researcher needs every filing extracted under a corrected ontology, a resolver that merges same-entity nodes without merging related ones, a record of every merge that can be undone, and a precision and recall figure honest enough to put in the README. Most GraphRAG projects publish no such number, and the open-source tools surveyed merge destructively with no log (research note, section 7).

## Solution

The step has two parts, in order.

**Part 1, extraction.** Ontology version 3 adds a MarketPlatform label (ADR-0005), puts the filer's CIK into Segment and MarketPlatform keys, and adds an `aliases` list to extracted nodes that span filings. The extract prompt becomes `extract/v2`. Extraction then runs in this order:
1. NVDA FY2026 10-K again, under v2.
2. AMD FY2025 10-K.
3. Review round 3 on AMD, judged by the user, with the miss check and a written review standard.
4. The other 21 filings, 10-Qs included, once round 3 scores about 85% or better.

The total extraction cost is capped at $40.

**Part 2, resolution.** A new `resolve` package reads all 24 extraction outputs and resolves nodes label by label:
1. Authoritative keys first. A filer extracted as an Organization maps to its Company by CIK.
2. Every same-label pair gets cheap scores. A pair becomes a candidate pair when any of these rules fires:
   - normalized names match;
   - fuzzy string similarity is high;
   - an acronym rule matches;
   - the names share a rare word;
   - one is an embedding neighbour of the other.

   MetricValues pair only within one filing, on equal subject, period, value and unit.
3. The adjudicator (claude-sonnet-5-5) judges each candidate pair `same`, `related`, `distinct` or `unsure`, quoting both nodes' chunks.
4. `same` decisions form groups. A group with any contradiction, or more than 5 names, is held back.
5. Every merge goes into an append-only merge log, and the alias map is rebuilt from the log (ADR-0006).

Nothing is written to Neo4j.

**Scoring.** A labeller agent (gpt-6-luna) labels a tuning set, the rules and prompt are tuned and frozen, and then it labels 100 scoring pairs drawn in three strata. `python -m resolve.score` reports:
- pairwise precision and recall on the final groups, weighted by stratum, with bootstrap 95% intervals;
- the adjudicator's own accuracy;
- the share of true matches the candidate rules found.

Every figure carries the label "agent-labelled (gpt-6-luna)".

The stop condition is met when these numbers are measured and written into the README, with the commit and config.

## User Stories

### Ontology version 3 and extraction

1. As the researcher, I want a MarketPlatform label separate from Segment, so that "the Gaming segment" and NVIDIA's Gaming market platform are different nodes.
2. As the researcher, I want Segment to mean only a reportable segment, so that segment questions return the segments a filer's segment note reports.
3. As the researcher, I want `HAS_MARKET_PLATFORM` from Company to MarketPlatform, so that a filer's market platforms are reachable from it.
4. As the researcher, I want MarketPlatform allowed as the subject of `MEASURES`, so that "Data Center revenue" is a figure for the Data Center platform.
5. As the researcher, I want MarketPlatform allowed as an endpoint of `AFFECTS` and as the start of `OFFERS`, so that risk factors and products can attach to platforms.
6. As the researcher, I want Segment and MarketPlatform keys to include the filer's CIK, so that two companies' same-named segments never share a node.
7. As the researcher, I want an `aliases` list on extracted nodes that span filings, so that the names merged into a node are kept and Step 11 can link a question to it by any of them.
8. As the researcher, I want the ontology version to be 3 and the schema hash to change, so that `apply_constraints()` and `:GraphMeta` notice the change.
9. As the researcher, I want the extract prompt to become `extract/v2`, with the old prompt kept, so that run reports from Step 8 still name a prompt that exists.
10. As the researcher, I want the v2 prompt to tell the model the difference between a reportable segment and a market platform, so that run 1's mislabelling does not recur.
11. As the researcher, I want the extractor to fill the filer's CIK into Segment and MarketPlatform keys in code, so that the model never builds keys.
12. As the researcher, I want `extract.run` to accept 10-Q filings, so that all 24 corpus filings can be extracted.
13. As the researcher, I want each run's estimate checked against `--max-cost` as before, so that no single run costs more than $10 by surprise.
14. As the researcher, I want a record of total extraction spend across Step 9's runs, so that I can see the $40 cap is respected.
15. As the researcher, I want NVDA FY2026 re-extracted under v2 before anything else, so that the filing already reviewed shows whether the market-platform fix works.
16. As the researcher, I want AMD's FY2025 10-K extracted second, so that the prompt is tested on a filer it was not written against.

### Review round 3

17. As the reviewer, I want review round 3 on the AMD extraction, so that the extraction accuracy figure covers more than one company.
18. As the reviewer, I want the review tool to show the review standard before I start, so that I judge borderline triples the way round 2 did.
19. As the reviewer, I want a span that names only one end to count as correct when the same chunk names the other end, so that rounds 2 and 3 are comparable.
20. As the reviewer, I want a shortened role to count as correct when it is the start of the full title and does not mislead, so that rounds 2 and 3 are comparable.
21. As the reviewer, I want the miss check measured properly this round, with the pace prompt applying to it, so that the extraction miss rate is finally a number.
22. As the researcher, I want the remaining 21 filings extracted only after round 3 scores about 85% or better, so that money goes on a prompt that works.
23. As the researcher, I want a round 3 below about 85% to send the prompt back for a fix before extraction continues, so that a bad prompt is not applied to 21 filings.

### Normalization and authoritative keys

24. As the researcher, I want a filer extracted as an Organization ("NVIDIA Corporation" in an AMD filing) mapped to its Company by CIK, so that competitor edges are not split between two nodes.
25. As the researcher, I want the filer-name table hand-written and small, so that each alias is a deliberate choice.
26. As the researcher, I want legal suffixes ("Inc.", "Corp", "Co., Ltd.", "N.A.", "LLP" and the like) stripped repeatedly from the end of a name for matching, so that "Huawei Technologies Co. Ltd." matches "Huawei Technologies".
27. As the researcher, I want the stored name left as the filing printed it, so that suffix stripping never changes what a reader sees.
28. As the researcher, I want stripping never to empty a name, so that "Company" on its own is not lost, as it is with cleanco.

### Candidate pairs

29. As the researcher, I want cheap scores computed on every same-label pair, so that pairs outside the candidate set can still be sampled for recall.
30. As the researcher, I want only candidate pairs to cost an LLM call, so that "never compare all pairs" holds where it costs money.
31. As the researcher, I want an exact match after normalization to make a candidate pair, so that "Groq" and "Groq, Inc." are caught.
32. As the researcher, I want a RapidFuzz `token_set_ratio` cutoff (90 to start, tuned) to make a candidate pair, so that word-order and extra-word variants are caught.
33. As the researcher, I want an acronym rule, so that "GDPR" meets "General Data Protection Regulation" and "OBBBA" meets "One Big Beautiful Bill Act".
34. As the researcher, I want a shared rare word to make a candidate pair, so that names that share a distinctive word ("Hon Hai", "Foxconn") are not missed.
35. As the researcher, I want the top-k Voyage neighbours of each node to make candidate pairs, so that matches with no shared spelling can still be found.
36. As the researcher, I want the embedded text ("label: name", or the name plus one evidence span) chosen on the tuning set, so that the choice rests on measured recall.
37. As the researcher, I want embeddings batched, given the same `input_type` on both sides, and cached on disk, so that reruns cost nothing.
38. As the researcher, I want MetricValues paired only within one filing, on equal subject, period, value and unit, so that "Revenue" and "Total revenue" for FY2026 meet and figures from different filings never do.
39. As the researcher, I want each candidate pair to record which rules produced it, so that the report can say which rule finds which matches.

### Adjudication

40. As the researcher, I want the adjudicator to see both names, the label, and each node's evidence spans and chunk text, so that it judges with the filing's own words.
41. As the researcher, I want the adjudicator to answer `same`, `related`, `distinct` or `unsure`, so that a product family and its member are not forced into "same".
42. As the researcher, I want a `related` answer to carry its kind (`family_member`, `parent_subsidiary` or `variant`), so that related pairs can feed a later ontology proposal.
43. As the researcher, I want the adjudicator to quote evidence from each node's chunks, checked in code like Step 8 spans, so that a merge rests on text that exists.
44. As the researcher, I want only `same` with verified quotes to merge, so that a wrong merge, which does more harm than a duplicate, is rare.
45. As the researcher, I want unverifiable quotes and `unsure` answers counted by reason, so that their cost to recall is visible.
46. As the researcher, I want the adjudicator to run as claude-sonnet-5-5 at effort `high` with structured output through the response cache, so that reruns reproduce decisions at no cost.
47. As the researcher, I want the prompt in `prompts/resolve/v1.md`, pinned by hash, with its version recorded on every decision, so that a prompt change is never silent.
48. As the researcher, I want the same-entity rule written into the prompt: same thing at the same level; a family and its member differ; a parent and subsidiary merge only where the filing uses the names interchangeably. So that the adjudicator applies the glossary's definition.
49. As the researcher, I want a Person pair such as "Mr. Teter" and "Timothy S. Teter" judged from their chunks, so that a short form merges only when the filing ties it to the full name.

### Groups, merge log and alias map

50. As the researcher, I want `same` decisions joined into groups, so that A=B and B=C put A, B and C on one node.
51. As the researcher, I want a group held back when any two members were judged `related` or `distinct`, so that a contradiction never becomes a merge.
52. As the researcher, I want a group of more than 5 names held back, so that a runaway chain of merges is caught.
53. As the researcher, I want held-back groups listed in the run report with every decision involved, so that I can see what was left unmerged and why.
54. As the researcher, I want the canonical key to be the member with the most evidence chunks, ties to the smallest key, so that replaying the log always gives the same map.
55. As the researcher, I want every merge in an append-only merge log with its deciding evidence, method (deterministic, LLM or user), prompt version and timestamp, so that each merge can be audited.
56. As the researcher, I want a merge undone by appending a split, so that no history is ever lost.
57. As the researcher, I want the alias map rebuilt by replaying the merge log, so that the log is the only source of truth.
58. As the researcher, I want every extracted key left unchanged, so that a split can restore it (ADR-0006).
59. As the researcher, I want a resolution rerun over unchanged inputs to append nothing new, so that the log does not fill with repeats.
60. As the researcher, I want the merge log, alias map, labelled pairs and run reports committed under `benchmarks/resolution/`, so that the published number can be checked.

### Labelling and scoring

61. As the researcher, I want a labeller on gpt-6-luna at effort `high`, a different model family from the adjudicator, so that the scores do not measure a model agreeing with itself.
62. As the researcher, I want the labeller to see names, label, evidence spans and chunk text, and never the resolver's decision or scores, so that its labels are independent.
63. As the researcher, I want each label to give same or different, the deciding quote from each side, and a reason, so that a reader can check any label.
64. As the researcher, I want the labeller's prompt in `prompts/label/v1.md`, pinned by hash, so that labels are reproducible.
65. As the researcher, I want labels written once to a committed file and never regenerated, so that the scoring set cannot drift.
66. As the researcher, I want a tuning set of about 100 pairs drawn and labelled first, so that cutoffs, k, the embedded text and the prompt are tuned on it alone.
67. As the researcher, I want the rules and prompt frozen, with their hashes recorded, before the scoring set is drawn, so that the scored figures were never tuned on.
68. As the researcher, I want 100 scoring pairs drawn from pairs not in the tuning set, stratified 60 candidate pairs / 25 just below the cutoff / 15 the rest, with a fixed seed, so that recall counts matches the rules missed.
69. As the researcher, I want pairwise precision and recall computed on the final groups, so that merges made through chains count too.
70. As the researcher, I want each stratum weighted by its size, with raw counts per stratum shown, so that a reader sees how much rests on 15 pairs.
71. As the researcher, I want bootstrap 95% intervals with a fixed seed, so that the figures carry their uncertainty.
72. As the researcher, I want the adjudicator's own accuracy and the share of true matches the rules found as separate rows, so that a recall loss can be traced to the adjudicator or to the rules.
73. As the researcher, I want every figure labelled "agent-labelled (gpt-6-luna)", so that nobody reads it as a hand-labelled result.
74. As the researcher, I want the scored figures written into the README with the commit and config, so that the stop condition is met honestly (invariant 2).

### Commands, cost and provenance

75. As the researcher, I want `python -m resolve.run` to check its API keys and refuse when `ANTHROPIC_BASE_URL` is set, so that it never runs against the wrong endpoint.
76. As the researcher, I want the labelling command to check `OPENAI_API_KEY` and refuse when `OPENAI_BASE_URL` is set, as `eval.run` does.
77. As the researcher, I want each paid command to show an estimate and refuse to run over `--max-cost`, so that a mistake cannot run up a bill.
78. As the researcher, I want each command's report to hold commit, prompt versions and hashes, models, efforts, ontology version, counts, tokens and cost, so that every number has provenance.
79. As the researcher, I want `resolve/` added to `git_state()`'s +dirty list, so that runs from uncommitted resolution code are marked.
80. As the researcher, I want adjudicator, labeller and embedding calls traced in Phoenix when tracing is on, so that cost and latency can be inspected like other calls.
81. As the researcher, I want distinct exit codes (0 done, 2 bad input, 3 could not run, 4 checks failed), as the other commands have, so that scripts can tell the cases apart.

## Implementation Decisions

### Ontology version 3 (ADR-0005)
- `ONTOLOGY_VERSION` becomes 3. New label MarketPlatform, extracted, with a one-line description: "a market a filer reports revenue by that is not a reportable segment". Segment's description says "reportable segment, as the filer's segment note defines it".
- New edge type `HAS_MARKET_PLATFORM` Company→MarketPlatform. MarketPlatform joins the end labels of `MEASURES` and `AFFECTS` and the start labels of `OFFERS`.
- Extracted labels other than RiskFactor and MetricValue gain an optional `aliases` property, a list of strings. It is empty or absent until Step 10 loads through the alias map.
- Key building: Segment and MarketPlatform keys are `{label}:{cik}:{norm(name)}`, built in code from the filing's CIK. Other cross-filing labels keep `{label}:{norm(name)}`. A MetricValue whose subject is a Segment or MarketPlatform carries that subject key, as it does now.
- The schema text and its hash change, so the prompt version hash changes too. The local Neo4j's v1 `:GraphMeta` is still the user's to clear before Step 10 (MEMORY note).

### Extraction
- `prompts/extract/v2.md` is a new file. v1 stays for past reports. v2 adds the segment-versus-market-platform distinction with NVIDIA's split as the example.
- `extract.run` accepts 10-Q filings. Its docstring and help text drop "10-K". The output and report paths stay `data/extract/{accession_no}-run{N}.jsonl` and `benchmarks/extraction/{accession_no}-run{N}.json`.
- Total spend: a committed ledger of each run's cost, or a sum over the committed run reports, is shown before each run. A run that would push the total past $40 needs an explicit flag. The ticket chooses which, keeping it small.
- `extract.review` shows the review standard (the two rules from user stories 19–20) before the first triple. Round results go to `benchmarks/extraction/{accession_no}-round{N}.jsonl` as before.

### The `resolve` package
Modules, organised by job:
- **normalize**: legal-suffix stripping (a hand-written list stripped repeatedly from the end, never to empty) and the match key: NFKC, casefold, punctuation and whitespace collapsed, suffixes stripped. Matching only; never stored as a name.
- **filers**: the hand-written filer-name table mapping names and aliases of NVIDIA, AMD and Intel to CIK.
- **candidates**: cheap scores for every same-label pair (normalized-equal, `token_set_ratio`, acronym, rare shared word, embedding rank), the rules that fired, and MetricValue pairing within one filing. The cutoffs, k and rare-word threshold come from one frozen config object whose hash goes in every report.
- **embed**: a batched Voyage path in `ingest/voyage.py` (same `input_type` both sides) behind the existing disk cache keyed by model and text hash. No new client.
- **adjudicate**: builds the request (both nodes' names, label, evidence spans and chunk text from `resolve()`), sends it through the Anthropic client and response cache with structured output, parses the reply, and checks each quote against its own node's chunks with Step 8's span normalization (extract's span checker is reused, not copied).
- **groups**: union-find over `same`, contradiction and size checks, and the canonical-key choice.
- **log**: the merge log format and `replay(log) -> alias map`.
- **pipeline**: the single entry point `resolve_nodes(nodes, embedder, adjudicator, config) -> Resolution`. A `Resolution` holds the new merge-log events, held-back groups, related pairs, and counts by rule, answer and reason. This is the main test seam.
- **label**: the labeller, through the existing OpenAI client path used by the judge (Responses API, strict json_schema, store=false, response cache).
- **sample**: the seeded draws of the tuning set and the stratified scoring set.
- **score**: `score(labels, alias_map, strata) -> ScoreReport`. Weighted pairwise precision and recall, fixed-seed bootstrap intervals (reusing the eval harness's bootstrap), adjudicator accuracy, and the candidate rules' share of true matches.
- **CLIs**: `python -m resolve.run`, `python -m resolve.label --set tuning|scoring`, `python -m resolve.score`. Each has `--max-cost` where it spends money, an estimate before calls, a provenance header in its report, and exit codes 0/2/3/4.

### Data contracts
- **Merge log line** (JSONL, append-only): `event` (`merge` | `split`), the two keys or the group, `method` (`deterministic` | `llm` | `user`), `rule` or `decision` id, the deciding quotes with chunk IDs, prompt version, config hash, commit, timestamp. A split names the merge it reverses.
- **Alias map**: every extracted key to its canonical key, generated by `replay()` and committed beside the log. A test checks that the committed map equals `replay(committed log)`.
- **Adjudicator reply** (structured output): `answer` in {same, related, distinct, unsure}; `related_kind` when `related`; `quote_a`, `chunk_id_a`, `quote_b`, `chunk_id_b`; `reason`.
- **Labelled pair line**: pair keys, label, set (`tuning` | `scoring`), stratum, `same` (bool), quotes with chunk IDs, reason, labeller model, effort and prompt version.
- **Filer Organization merges** use method `deterministic` and record the table entry that matched.

### Dependencies and config
- `rapidfuzz==3.14.6` becomes a direct dependency (approved; already locked through edgartools). No other new packages. `cleanco`, Splink, dedupe and recordlinkage are rejected (research note, section 4).
- New config pins: adjudicator provider, model, effort and output cap; labeller model and effort; embedding model; the dated price table. Each report records them.

### Workflow
- One branch and PR per ticket, as since Step 6.
- Paid runs are started by the user or with their go-ahead. The agent shell sets `ANTHROPIC_BASE_URL`, so commands run with `env -u ANTHROPIC_BASE_URL` (MEMORY).
- Review round 3 runs in the Terminal panel from the main checkout, as rounds 1 and 2 did.

## Testing Decisions

- **Good tests** check what a caller sees from the five seams agreed for this step. They never assert on private helpers, prompt wording or the order of internal calls.
  1. **`extract_filing()` with `check_batch()` and `validate_graph()`** for ontology v3 and prompt v2. Recorded real replies for a few fixture chunks, including an NVIDIA segment note naming both segments and platforms, and one 10-Q chunk. Hand-written broken cases each break one thing: a MarketPlatform in a `HAS_SEGMENT`, a Segment key without a CIK, `aliases` of the wrong type, an `AFFECTS` to an unlisted label.
  2. **`extract.review`**, driven with injected `ask`, `out` and a fake clock as now. Adds the standard shown before the first triple, and the pace prompt on the miss check.
  3. **`resolve_nodes()`**, with recorded Voyage vectors and recorded claude-sonnet-5-5 replies for real pairs from run 1 (Groq, Huawei, GDPR / U.K. GDPR, OBBBA, Teter, Blackwell / GB200, GeForce / GeForce NOW, Revenue / Total revenue), plus hand-written replies that each break one rule: a quote not in the chunk, a quote from the wrong node's chunks, `unsure`, `related` without a kind, invalid JSON. Cases cover:
     - each candidate rule firing alone;
     - suffix stripping, including "Co., Ltd." and a bare "Company";
     - a filer Organization mapped to its Company;
     - MetricValue pairs only within one filing;
     - a contradiction and a 6-name group held back;
     - the canonical-key choice and its tie-break;
     - a rerun appending nothing.
  4. **`replay()`**: merge then split restores both keys; replay is the same every time; a split of an unknown merge is rejected; the committed alias map equals the replay of the committed log.
  5. **`score()`**, against hand-computed values: weighted precision and recall over three strata, a merge made through a chain counting as predicted-same, an empty stratum, the fixed-seed interval, and the rules' share of true matches.
- **Labeller** tests replay recorded gpt-6-luna replies through the command, and check that the request carries no resolver output.
- **Commands**: exit codes 2 and 3 (missing key, base URL set, estimate over the cap, database unreachable) and 4. A cached run makes zero calls.
- **Offline.** All tests run offline under pytest-socket. Database-backed tests use the throwaway Postgres schema and skip without `DATABASE_URL`, as now.
- **Fixtures.** Chunk text comes from the gzipped real filings. Recorded replies are model replies, not filings, as in Step 8.
- **TDD.** For each deterministic piece (suffix stripping, candidate rules, groups, replay, scoring), the test is shown failing before the code exists.
- **Prior art:**
  - `tests/test_extract_recorded.py` and `tests/extract_fakes.py` for recorded replies;
  - `tests/test_judge_openai.py` and `tests/openai_fixtures.py` for the labeller;
  - `tests/test_extract_review.py` for the review tool;
  - `tests/test_extract_merge.py` for merge and conflict cases;
  - `tests/test_bootstrap.py` for intervals;
  - `tests/test_extract_run.py` and `tests/test_gate.py` for exit codes.

## Out of Scope

- Writing anything to Neo4j, loading through the alias map, and filling `aliases` on nodes (Step 10).
- `PERSISTS_AS` and any RiskFactor matching across filings (Step 10).
- Mapping MetricValue concepts to XBRL tags or calling companyfacts (OPEN-DECISIONS, Lookup arm).
- Turning `related` pairs into edges such as product family or parent company. That would be a later ontology proposal.
- A user override command for held-back groups. The log's `user` method allows one later.
- Any human check of the labeller's labels (user decision, 2026-10-10).
- B-cubed or other cluster metrics.
- The Haiku extractor ablation and filtering retrieval by confidence (OPEN-DECISIONS).
- New tickers or data sources.

## Further Notes

- **Cost.** Extraction is capped at $40 in total. NVDA's run 1 cost $2.23, and 10-Qs are shorter than 10-Ks. Adjudication covers a few hundred candidate pairs at most; run 1's lexical rules alone flagged 49 of 2,139 pairs. Labelling is about 200 calls. Both should cost a few dollars, and the estimates are shown before each run.
- **Intervals at n=100 are wide.** 45 of 50 correct has a Wilson interval of 0.79 to 0.96, and 0 matches in 15 random pairs bounds that stratum's match rate only below about 0.20. Report the raw counts beside the weighted figures.
- **Order of work:** ontology v3 → prompt v2 and 10-Q support → NVDA again → AMD → review round 3 (the user, about 35 minutes) → the other 21 filings → resolution, tuning set, freeze → scoring set → score → README. A round 3 under about 85% loops back to the prompt.
- **Agent-labelled.** The RUNBOOK asks for hand labels. The user chose an agent labeller from another model family, with no human check. CLAUDE.md records this, and the README states it next to the number.
- **Review round 3 counts as extraction accuracy** only through the user's judgments, as in Step 8. An agent may flag triples it thinks wrong, labelled as a second look.
