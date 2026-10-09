# Step 8: Extraction

Status: ready-for-agent
Sources: RUNBOOK Step 8 (with its 2026-10-09 note), docs/GRAPH-LAYER.md (Indexing pipeline, Prompts), ADR-0004, docs/research/claude-extraction-structured-output.md, docs/OPEN-DECISIONS.md, and the grilling session of 2026-10-09, whose decisions are in CLAUDE.md Decisions (Step 8 entries). External research: docs/research/claude-extraction-structured-output.md covers the model, structured outputs, effort, prompt caching, prices, confidence and GraphRAG's extraction design.

## Problem Statement

Step 7 defined what the graph may hold and built a write path that refuses anything outside the ontology or without evidence. The graph is still empty. Nothing yet reads the filings and proposes nodes and edges.

An extractor is where a graph project usually goes wrong. A model asked to find entities will invent types, paraphrase its quotes, guess at keys and sound equally sure of everything. If its output is trusted without reading it, the graph inherits those errors, and every graph arm in Steps 11 to 13 is built on them. The RUNBOOK's warning applies: the first prompt will be wrong in a way nobody anticipated, and only reading its output line by line finds out how.

The researcher needs an extractor whose output is checked mechanically before any human looks at it, a way to read a sample of what passed against the source text at a careful pace, and a recorded accuracy figure honest enough to publish.

## Solution

A new `extract` package runs Prompt 1 over every chunk of one 10-K (NVDA FY2026 first). Each call sends one chunk with the ontology's schema text and receives structured output: candidate nodes and candidate triples, each with an evidence span and a confidence level. Code then does everything the model must not:
- maps filer references to CIKs and the accession number;
- builds node keys;
- checks every evidence span verbatim against the chunk's text from `resolve()`;
- converts survivors into write-path records and runs `check_batch()` on the whole filing as a dry run.

Nothing is written to Neo4j in this step.

`python -m extract.run` produces the candidates file and a committed run report with counts, rejections by reason, conflicts, tokens and cost. `python -m extract.review` lets the researcher judge a review round of 30 validated edges against their chunks, plus a miss check on 5 whole chunks. The results are committed and summarised in BUILD-LOG with a Wilson 95% interval.

The ontology moves to version 2. Extracted edges and `EVIDENCED_BY` edges carry each piece of evidence's confidence and the prompt version that produced it.

The stop condition is met when:
- the extraction has run over the NVDA FY2026 10-K;
- the last review round scores about 85% or better (all rounds reported);
- `check_batch()` reports zero violations on the whole filing.

## User Stories

### Running extraction

1. As the researcher, I want one command that extracts a whole 10-K by accession number, so that a run is one repeatable action.
2. As the researcher, I want the command to print an estimated cost before any call and refuse when the estimate exceeds a cap (default $10), so that a mistake in the prompt or chunk count can't run up a bill.
3. As the researcher, I want the estimate to allow for Claude's tokenizer counting more tokens than cl100k, so that the cap isn't beaten by a known undercount.
4. As the researcher, I want the command to stop at start when `ANTHROPIC_API_KEY` is missing, so that a run never fails half way through.
5. As the researcher, I want the command to refuse to start when `ANTHROPIC_BASE_URL` is set, so that filing text and the key never go to an endpoint I didn't choose.
6. As the researcher, I want every response stored in the existing response cache, so that a rerun of the same prompt version makes no new calls and costs nothing.
7. As the researcher, I want the system prompt marked for prompt caching, so that the ontology text is billed at the cache-read rate after the first call.
8. As the researcher, I want calls made 4 at a time, so that a 10-K finishes in minutes without hitting rate limits.
9. As the researcher, I want each chunk to be extracted independently, so that one bad chunk can't change another chunk's output.
10. As the researcher, I want the run to finish and report when individual chunks fail (API error, unparseable reply), with the failed chunk IDs listed, so that one error doesn't lose the rest of the run.
11. As the researcher, I want a run with failed chunks reported as incomplete, never as a pass, so that the stop condition can't be met on part of a filing.
12. As the researcher, I want exit codes that match the other commands (0 complete, 2 bad arguments, 3 could not run, 4 violations or failed chunks), so that the command behaves like `eval.gate` and `graph.validate`.
13. As the researcher, I want every chunk of the filing extracted, preamble and statement tables included, so that segment figures in the financial statement notes are not skipped.

### The prompt and the model

14. As the researcher, I want the prompt in a versioned file next to the answer prompt, with any edit creating a new version, so that every triple can be traced to the prompt that produced it.
15. As the researcher, I want the prompt to carry the schema text generated from the ontology module, never a hand-written copy, so that the prompt can't drift from the ontology.
16. As the researcher, I want the output schema generated from the ontology too, with labels and edge types as enums, so that the model can't name a type outside it.
17. As the researcher, I want the extractor to be `claude-sonnet-5-5` at effort `high` with no sampling settings, so that it matches the decision and the API's rules for this model.
18. As the researcher, I want the prompt to tell the model to emit nothing the chunk doesn't state, so that the graph holds disclosures, not guesses.
19. As the researcher, I want the prompt to say that forward-looking statements are risks a filing discloses, never figures it reports, so that a projection is never stored as a reported number.
20. As the researcher, I want MetricValue limited to figures with a named subject (filer, segment or product), a period and a unit, so that the graph doesn't fill with unlabelled table cells.
21. As the researcher, I want the model to refer to this filing, the filer and the other two filers by fixed references, so that NVIDIA named in AMD's filing becomes the Company node, not an Organization.
22. As the researcher, I want any firm other than the three filers to be an Organization, so that supplier, customer and competitor stay edges.
23. As the researcher, I want the model never to emit Chunk or Period nodes, so that structural nodes come only from EDGAR metadata.
24. As the researcher, I want each candidate to carry a confidence of `stated`, `implied` or `uncertain`, defined in the prompt by what the text does, so that the level means something checkable.
25. As the researcher, I want enum values compared without regard to case, so that the API's casing freedom isn't counted as a violation.

### Mechanical checks

26. As the researcher, I want every returned label, edge type and endpoint pair checked against the ontology before anything else, with each violation discarded, logged and counted, so that the violation rate is reported, not hidden.
27. As the researcher, I want every evidence span checked against `resolve(chunk_id)`, so that the check uses the stored filing text, not text the model echoed.
28. As the researcher, I want spans matched after NFKC, curly quotes and dashes mapped to ASCII, and whitespace collapsed, but case-sensitively and with no fuzzy matching, so that a paraphrase is caught.
29. As the researcher, I want a node whose span fails to be rejected with every candidate triple touching it, so that no edge hangs off an unevidenced node.
30. As the researcher, I want a node whose name isn't in its own span flagged rather than rejected, so that "the Company" is allowed but visible in review.
31. As the researcher, I want unknown filer references, missing required properties and wrong property types rejected with their own reasons, so that every failure mode has a count.
32. As the researcher, I want rejections counted by reason, per run, with examples, so that I can see which part of the prompt is failing.
33. As the researcher, I want the survivors converted to write-path records and passed through `check_batch()` for the whole filing, so that "zero violations reaching the write path" is tested against the real guard.
34. As the researcher, I want any `check_batch()` violation at that point treated as a bug in the extractor's own checks and reported loudly, so that the stop condition can't be met with a silent gap.

### Keys, merging within a run, and conflicts

35. As the researcher, I want node keys built by code from the label and a normalized name, so that "TSMC" in two chunks becomes one node.
36. As the researcher, I want normalization limited to NFKC, case folding and collapsed whitespace, so that suffix stripping stays in Step 9, where its precision and recall are measured.
37. As the researcher, I want RiskFactor keys to include the accession number and MetricValue keys to include the accession number, subject, metric and period, so that ADR-0004's per-filing nodes hold.
38. As the researcher, I want the same node or edge from several chunks merged into one record with one evidence entry per chunk, so that the candidates match what the write path will store.
39. As the researcher, I want a single-valued property (a name, a stake, a title) that differs between chunks to take the value from the most confident evidence (`stated` > `implied` > `uncertain`, ties to the latest chunk) and be counted as a conflict with both values shown, so that disagreement is visible, never silently dropped.
40. As the researcher, I want `MetricValue.concept` to be the metric as the filing names it, normalized, so that nothing looks like an XBRL tag when it isn't one.

### Ontology version 2

41. As the researcher, I want extracted edges to carry `confidences` and `extract_prompts` lists parallel to `chunk_ids` and `evidence_spans`, so that confidence and prompt version belong to each piece of evidence.
42. As the researcher, I want `EVIDENCED_BY` to carry `confidence` and `extract_prompt`, so that node evidence has the same detail.
43. As the researcher, I want the write path's checks and `validate_graph()` to require the new properties, check list lengths and reject confidence values outside the enum, so that version 2 is enforced like version 1.
44. As the researcher, I want `ONTOLOGY_VERSION` set to 2 with the schema hash test updated, so that a graph built under version 1 is refused.
45. As the researcher, I want the prompt version written as `extract/v1@<hash8>`, so that the exact prompt text can be found from any edge.

### Reviewing

46. As the researcher, I want a review command that only I run, refusing non-interactive input, so that only my judgments count as extraction accuracy.
47. As the researcher, I want 30 validated edges sampled at random with a fixed seed, stratified by edge type with at least one of each type present, so that the sample reflects the run without one type filling it.
48. As the researcher, I want each round to draw edges I haven't judged before, so that a second round measures the revised prompt on fresh output.
49. As the researcher, I want each triple shown with its start node, edge type, end node, properties, confidence and flags, followed by the full chunk text with the span highlighted, so that I judge it against the source.
50. As the researcher, I want to answer correct, wrong or skip, with skips not counting toward 30, so that I'm never forced to guess.
51. As the researcher, I want a one-line reason required for every wrong answer, so that the round tells me what to fix in the prompt.
52. As the researcher, I want correct to mean the right labels, direction, properties and a span that states it, with partly right counted as wrong, so that the bar is the one the graph needs.
53. As the researcher, I want a "take another look?" prompt when I answer in under 15 seconds, and the round's median time reported, so that pace is visible.
54. As the researcher, I want to be shown 5 random whole chunks with all their extracted triples and asked whether anything important is missing, so that a timid prompt can't pass on precision alone.
55. As the researcher, I want to be able to quit and resume a round without losing judgments, so that a round can span two sittings.
56. As the researcher, I want the round written to a committed file with each triple, verdict, reason, reviewer, time taken, prompt version and commit, so that the accuracy figure can be traced.
57. As the researcher, I want the round summary to give accuracy with a Wilson 95% interval, accuracy per confidence level and the miss rate labelled directional, so that the numbers are reported at their real precision.

### Reporting and records

58. As the researcher, I want a committed run report with commit, prompt version and hash, model, effort, ontology version, chunk count, candidates, rejections by reason, conflicts, flags, failed chunks, tokens (input, cache read, cache write, output) and cost, so that a run can be compared with the next.
59. As the researcher, I want cost computed from the token counts the API reported and a new dated price table that has the corrected Sonnet 5.5 cache-read price, with the old table kept, so that past costs still trace to the prices they used.
60. As the researcher, I want extraction calls traced in Phoenix like answer calls when tracing is on, so that slow or expensive chunks are visible.
61. As the researcher, I want `extract/` added to `git_state()`'s dirty check, so that a run from uncommitted extractor code is marked `+dirty`.
62. As the researcher, I want the candidates file gitignored and the run reports and review rounds committed, so that git holds the evidence of quality without the bulk output.

### The rewrite rule (recorded for Step 10)

63. As the researcher, I want Step 10's write path to replace a chunk's own evidence entry when the same chunk is extracted again, and to apply the most-confident-wins rule with conflict counts across chunks, so that the Step 7 latest-wins rule is replaced consistently once writing starts.

## Implementation Decisions

### Modules

- **New `extract` package.** It holds:
  - the prompt loader (same shape as the answer prompt loader: version, text, hash);
  - the output schema derived from the ontology;
  - the extraction request (implements the existing `ModelRequest` protocol, so the existing response cache and Anthropic client serve it);
  - span normalization and checking;
  - filer reference mapping;
  - key building;
  - candidate-to-record conversion;
  - the per-filing merge with conflict counting;
  - the run command;
  - the sampler and review command.

  Split by concern into small modules, as `graph/` is.
- **Main interface: `extract_filing(filing, chunks, model, prompt)`.** `filing` is the filing metadata including the filer's CIK and accession number. `chunks` are pairs of chunk ID and text from `resolve()`. `model` is any `AnswerModel`, normally cached. `prompt` is a loaded extract prompt. It returns an immutable `ExtractionRun` that holds:
  - the merged nodes and edges as write-path records;
  - rejections, each with chunk ID, reason and the offending candidate;
  - flags and conflicts;
  - per-chunk failures;
  - token usage;
  - the `check_batch()` result.

  It makes model calls through the injected model and does no file I/O.
- **Command `python -m extract.run --accession <no> [--max-cost USD]`.** This thin layer adds the environment guards, the cost estimate, the database reads (chunk texts through `resolve()`), the concurrency (4 workers, contextvars copied as the judge runner does, so spans keep their parent), writing the candidates file and writing the run report. It needs `DATABASE_URL` and `ANTHROPIC_API_KEY`. The run number N is the next free number for that accession.
- **Review: `review(run_candidates, previous_rounds, ask, out, clock, seed)` and `python -m extract.review --reviewer "<name>" --accession <no>`.** Built like `eval.review`: injected `ask` and `out`, refusing when stdin is not a TTY. Resuming continues the open round's file. The command never runs inside an agent session; agents build and test it with injected inputs only. Amended 2026-10-09 (ticket 04): the function is `review(candidates, chunks, rounds_dir, *, reviewer, commit, ask, out, clock, seed)` and reads earlier rounds from `rounds_dir`; `--run N` picks a run (default the latest). An edge read from several chunks is judged against one of its evidence entries, drawn with the seed, and accuracy per confidence level uses that entry's confidence. A slot whose type runs out after skips draws from any type, and each judgment records its slot's type. Skips are written as `skip` lines, get no "take another look?" prompt, and a skipped edge may be drawn in a later round, since it was never judged. Time per answer runs from showing the triple to the final verdict. The summary is the round file's last line (`kind` summary), which also marks the round closed.
- **Schema derivation.** A function derives the JSON schema for the structured output from `graph/ontology.py`. It must stay inside the structured-output limits in the research note: no numeric or length bounds, `minItems` only 0 or 1, at most 24 optional and 16 union-typed parameters. A test checks the generated schema against those limits.
- **Structured output.** Requests use `output_config.format` with the derived JSON schema and `output_config.effort = "high"`, sent through the existing Anthropic client so the response cache stores the raw body. The reply text is parsed and validated locally, and any parse failure is a per-chunk failure. This is the same API feature `messages.parse` uses. Going through the existing client keeps one cache and one client for all Claude calls.
- **Request parameters.** `max_tokens` 8,000, no temperature, top_p or top_k, no tool use, one cache breakpoint at the end of the system prompt. The cache key covers the whole request, so a prompt edit misses the cache by construction.
- **Candidate shape returned by the model** (per chunk). A list of nodes, each with a local ID, label, name or title, other properties, evidence span and confidence. A list of triples, each with a start and end (a local node ID or a filer reference), an edge type, properties, evidence span and confidence. Filer references are `THIS_FILING`, `FILER`, `NVDA`, `AMD` and `INTC`.
- **Keys.**
  - `norm(s)` is NFKC, then casefold, then collapsed whitespace.
  - Cross-filing labels: `{label}:{norm(name)}`.
  - RiskFactor: `{accession_no}:{norm(title)}`.
  - MetricValue: `{accession_no}:{subject key}:{norm(concept)}:{period}`, where the subject key is the CIK for a filer or the built key for a Segment or Product.

  The exact separator escaping must make keys unambiguous; a test covers a name containing the separator. Implemented (ticket 02): `%` and `:` in names, concepts and periods are percent-encoded; a subject key keeps its own separators, which stays unambiguous because the accession number before it has none and the two parts after it are escaped. The period is escaped but not normalized.
- **Span check.** Both sides go through NFKC, curly single and double quotes to ASCII, en and em dashes to `-`, and whitespace collapsed to one space and trimmed. Then a case-sensitive substring test. The span check uses the text `resolve()` returns. Amended 2026-10-09 (user decision, ticket 02): markdown escapes (a backslash before ASCII punctuation) are dropped on both sides too, and from every text value the model returns, so names and keys never carry them. A match stores the chunk's own text for the span.
- **Rejection reasons.** These are a closed enum, each counted:
  - unknown label;
  - unknown edge type;
  - endpoint pair not allowed;
  - unknown filer reference;
  - dangling local node ID;
  - span not found;
  - endpoint node rejected;
  - missing required property;
  - wrong property type;
  - structural label emitted.
- **Flag reasons.** These are counted and shown in review, never rejected: name not in span, and confidence `uncertain`.
- **Per-filing merge.** Records are merged on the node key and on (start key, type, end key, role) for edges, per ADR-0004. Evidence is appended per chunk, with one entry per chunk. Conflicts follow user story 39.
- **Ontology version 2.**
  - `ONTOLOGY_VERSION = 2`.
  - Extracted edges gain required `confidences` (LIST<STRING>) and `extract_prompts` (LIST<STRING>).
  - `EVIDENCED_BY` gains required `confidence` (STRING) and `extract_prompt` (STRING).
  - Confidence values are restricted to `stated`, `implied` and `uncertain`, a new value rule enforced by `check_batch()` and `validate_graph()`.
  - `Evidence` and `EdgeRecord` evidence carry the new fields.
  - The Step 7 test graph is updated to version 2.
  - Applying version 2 constraints to a Neo4j whose `:GraphMeta` says version 1 is refused, as now.

  The local Neo4j holds no extracted data, so the researcher clears its `:GraphMeta` by hand when Step 10 starts. That is recorded, not automated here.
- **Pricing.** A new dated table, dated to the research note's reading, with Sonnet 5.5 cache reads at $0.10 per MTok. Cost functions take the table that applies, and every report records which table and date it used.
- **Output locations.**
  - Candidates: `data/extract/{accession_no}-run{N}.jsonl`, gitignored.
  - Run reports: `benchmarks/extraction/{accession_no}-run{N}.json`.
  - Review rounds: `benchmarks/extraction/{accession_no}-round{N}.jsonl`.
- **Tracing.** Each chunk call is an LLM span with model, tokens, cost, cache hit and chunk ID. Chunk text and reply text only go on spans under `FILINGINTEL_TRACE_TEXT=true`. Tracing is off unless `PHOENIX_COLLECTOR_ENDPOINT` is set, as in Step 6.
- **Provenance.** `extract/` is added to `git_state()`'s dirty list.
- **No new dependencies.** The anthropic SDK, psycopg and the tracing packages are already approved.

### Prompt iteration

- Prompt v1 is written first, run on the whole NVDA FY2026 10-K, and reviewed in round 1. A failed round leads to prompt v2 (a new file), a full rerun and a fresh round. Every round and run is kept and reported.
- AMD FY2025 is not run in this step unless the researcher asks after NVDA passes.

## Testing Decisions

- **Good tests here** check what a caller sees from `extract_filing()`, the review function, `check_batch()` and `validate_graph()`, and the command's exit codes and files. They never assert on private helpers, prompt wording or the order of internal calls.
- **`extract_filing()`.** Tests use chunks from the gzipped NVDA FY2026 fixture, chunked by the real chunker. Model replies come from two sources:
  - Recorded real responses from `claude-sonnet-5-5` for a small set of fixture chunks (a risk factor, a segment note, a Business paragraph naming suppliers, a statement table). They are captured once with the network on and replayed offline. These cover the happy path, filer references, merging across chunks and key building.
  - Hand-written broken replies, each breaking exactly one rule: an invented label, an invented edge type, a forbidden endpoint pair, a paraphrased span, a span with only case changed, an unknown filer reference, a dangling local ID, a structural label, a wrong property type, and a reply that isn't valid JSON. Each must be rejected with its own reason and leave the rest of the chunk's output intact.

  These are model replies, not filings; the rule that fixtures are real filings concerns filing text, and the filing text here is real.
- **Span normalization.** Tests use quotes taken from the fixture chunks: curly quotes, em dashes and line-wrapped whitespace must match, a changed word or changed case must not.
- **Conflicts.** Two chunks give different stakes or names at different confidence levels, covering winner, tie to latest, and the count.
- **Schema derivation.** The generated schema stays inside the structured-output limits and lists exactly the ontology's labels and edge types. Amended 2026-10-09 (ticket 02): exactly the extracted labels and the extracted edge types one chunk can evidence; `PERSISTS_AS` is left out (Step 10).
- **Ontology version 2.** These extend `tests/test_ontology.py`, `tests/test_graph_batch.py`, `tests/test_graph_write.py` and `tests/test_graph_validate.py`. New broken cases each break one thing: a missing `confidences` list, a list of the wrong length, a confidence outside the enum, an `EVIDENCED_BY` without `extract_prompt`.
- **Review.** Driven with injected `ask`, `out` and a fake clock, as `eval.review`'s tests are. Tests cover:
  - the seeded stratified sample, with at least one per type present;
  - no repeats across rounds;
  - required reasons;
  - skips not counting;
  - the under-15-second prompt;
  - resume after quit;
  - the miss check;
  - the Wilson interval at known values;
  - refusing non-interactive input.
- **Command.** Exit codes 0, 2, 3 (missing key, `ANTHROPIC_BASE_URL` set, database unreachable, estimate over the cap) and 4 (a failed chunk; a `check_batch()` violation). A cached run must make zero model calls.
- **Pricing.** Old and new tables give the costs they should, and a report records its table date.
- **Offline.** All tests run offline under pytest-socket, as CI requires. The database-backed command tests use the throwaway Postgres schema and skip without `DATABASE_URL`, as now.
- **TDD.** For each deterministic piece, the test is shown failing before the code exists.
- **Prior art:**
  - `tests/test_answer*.py` and the response cache tests for replayed model responses;
  - `eval.review`'s tests for the interactive tool;
  - `tests/test_graph_batch.py` for one-rule-broken cases;
  - `eval.gate` and `graph.validate` tests for exit codes.

## Out of Scope

- Writing anything to Neo4j. Loading is Step 10, and so is implementing the rewrite rule in user story 63.
- Entity resolution beyond the normalization above: suffix stripping, blocking, adjudication and the merge log are Step 9.
- `PERSISTS_AS` linkage across filings (Step 10).
- Mapping metrics to XBRL tags or using EDGAR companyfacts (OPEN-DECISIONS, Step 9).
- Extracting from any filing other than NVDA FY2026, and extracting from 10-Qs.
- Gleaning, the Batches API and a Haiku 5.5 challenger (OPEN-DECISIONS, after Step 8).
- Filtering retrieval by confidence (Step 11).
- Making `validate_graph()` a CI gate (Step 13).
- Any agent judgment counted as extraction accuracy.

## Further Notes

- Token counts in the research note's cost estimates are cl100k counts; Claude's own counts may be about 30% higher. The pre-run estimate should allow for this, and the run report gives the real figure.
- Expected cost of one full NVDA FY2026 run is about $4 to $5 at effort `high` with caching. A prompt revision reruns every chunk, because the prompt is part of the cache key.
- A round of 30 at about 45 seconds a triple, plus the miss check, takes about 35 minutes. Plan rounds for when there is time to read carefully.
- At n=30 the interval is wide: 26 of 30 (87%) has a Wilson 95% interval of 70% to 95%, and 25 of 30 (83%) has 66% to 93%. Passing needs 26 of 30. Report the count and the interval, not just the percentage.
- The RUNBOOK's 30-triple accuracy is precision; the miss check gives only a directional sense of recall. Graph-arm recall is measured in Step 11.
