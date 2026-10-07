# Step 6: CI quality gate and Phoenix tracing

Status: done (2026-10-07)
Sources: RUNBOOK Step 6, ADR-0003, docs/research/phoenix-tracing.md, and the grilling session of 2026-10-06, whose decisions are in CLAUDE.md Decisions (Step 6 entries).

## Problem Statement

Step 5 produced a measured control arm and a committed baseline run, but nothing protects them. A change that weakens retrieval, such as a smaller k, a broken question filter or a stale embedding, would pass lint and tests and only show up the next time someone runs the paid eval by hand. CI cannot run that eval: it has no network, no API keys, and an empty Postgres, and the eval's inputs live in the gitignored `data/` directory on the researcher's laptop.

There is also no way to see what one eval question did. The run file records totals and per-question results, but not the sequence of calls behind an answer: which filter applied, how long the search took, which judge call was slow or expensive.

## Solution

Two independent pieces.

- **Quality gate.** A new CI job, `quality-gate`, runs on every pull request and every push to `main`. It loads a committed snapshot of the corpus database into CI's Postgres and runs the control arm's real search on the 82 answerable `dev` questions, offline. It scores recall@5, recall@10 and the retrieval wrong-evidence rate per class and pooled, and compares them with the committed gate baseline. Any drop fails the build. A ruleset on `main` makes the job a required check, so a PR that weakens retrieval cannot merge.
- **Tracing.** Phoenix runs locally in Docker. When `PHOENIX_COLLECTOR_ENDPOINT` is set, every eval question becomes a trace: the question filter, query embedding, vector search, answer generation and each judge call are spans with OpenInference names, so Phoenix shows the call tree, latency, tokens and cost per question. When it is unset, nothing is traced and nothing is sent.

The RUNBOOK's stop condition is met by opening a PR that sets k to 1, watching the gate fail, and closing it.

## User Stories

### Quality gate

1. As the researcher, I want every pull request checked for a drop in retrieval scores, so that a regression is caught before it merges rather than at the next paid run.
2. As the researcher, I want the gate to make no API calls, so that a push never costs money.
3. As the researcher, I want the gate to keep CI's no-network rule, so that no API key is stored in GitHub.
4. As the researcher, I want the gate to run the control arm's real search code, not a copy, so that a change to that code is what the gate measures.
5. As the researcher, I want the gate to use the real database schema, so that a schema change that breaks search also breaks the gate.
6. As the researcher, I want the gate to use stored question vectors, so that it never calls Voyage and its input never changes between runs.
7. As the researcher, I want two gate runs on the same code to give identical numbers, so that a failure always means the code changed.
8. As the researcher, I want the gate to cover all 82 answerable `dev` questions, so that a regression on a few questions still shows.
9. As the researcher, I want decline and unanswerable questions left out of the gate, so that no score is computed for questions with no gold chunks.
10. As the researcher, I want the `test` split never read by CI, so that the held-out questions stay unseen until the final benchmark.
11. As the researcher, I want recall@5 and recall@10 gated per class and pooled, so that a multi-hop drop cannot hide inside a lookup gain.
12. As the researcher, I want the retrieval wrong-evidence rate gated per class and pooled, so that a change that ranks confusable chunks higher fails even when recall holds.
13. As the researcher, I want any drop at all to fail, so that small real regressions don't build up unseen.
14. As the researcher, I want a failing gate to name each class and number that dropped, with the before and after values, so that I can see what broke without rerunning anything.
15. As the researcher, I want the gate to show its full comparison table in the GitHub job summary, so that I can read the result on the PR page.
16. As the researcher, I want the gate to fail with a clear message when the snapshot, the question set or the gate baseline don't match each other, so that a stale input is never mistaken for a pass.
17. As the researcher, I want to update the gate baseline with one command, so that a deliberate trade-off is recorded in the same PR that makes it.
18. As the researcher, I want the gate baseline to record the commit, the snapshot hash and the question set hash, so that I can tell what it was measured against.
19. As the researcher, I want a baseline update to show up as a plain diff in the PR, so that a lowered number is visible in review.
20. As the researcher, I want agents never to lower the gate baseline to get a PR through, and any lowering to need my yes and a BUILD-LOG line, so that the gate can't be quietly weakened (invariant 5).
21. As the researcher, I want the first gate baseline checked against the committed baseline run, so that the gate is shown to reproduce what `eval.run` measured.
22. As the researcher, I want the same gate command to run on my laptop against my local database, so that I can check a change before pushing.
23. As the researcher, I want a PR that sets k to 1 to fail the gate, so that the RUNBOOK's stop condition is met.
24. As the researcher, I want the gate to label its numbers "agent-drafted questions", so that no gate output is read as a human-verified result.

### Snapshot

25. As the researcher, I want the snapshot built by one command from my local database, so that rebuilding it after a chunker or embedding change is routine.
26. As the researcher, I want the snapshot to hold the real filings, chunks and chunk embeddings rows, so that CI loads them into the real schema with no CI-only tables.
27. As the researcher, I want the snapshot to hold the query vectors for the gated questions, so that the gate needs no network.
28. As the researcher, I want the snapshot to be one gzipped file, so that it is a single committed artifact with a single hash.
29. As the researcher, I want a test that fails when the committed snapshot no longer matches my local database, so that I notice when it needs rebuilding.
30. As the researcher, I want that test to skip in CI, where there is no local database, so that CI stays offline.
31. As the researcher, I want loading the snapshot to check its hash and contents, so that a damaged or hand-edited snapshot fails loudly.
32. As the researcher, I want the snapshot to hold no secrets and no data from outside EDGAR, so that committing it is safe.

### Pull requests and branch protection

33. As the researcher, I want one branch and PR per ticket, so that the gate shows which ticket caused a regression.
34. As the researcher, I want `main` protected by a ruleset that requires the gate, so that a failing PR cannot merge.
35. As the researcher, I want no bypass on that ruleset, so that skipping the gate means editing the ruleset, which leaves a record.
36. As the researcher, I want to be asked before any GitHub setting changes, so that I stay in control of the repository.
37. As the researcher, I want CI to run once per PR push and once per push to `main`, not twice, so that checks don't duplicate.
38. As the researcher, I want no path filters on the CI workflow, so that a required check is never stuck waiting for a skipped workflow.

### Tracing

39. As the researcher, I want each eval question traced as one tree of spans, so that I can see every call behind one answer.
40. As the researcher, I want spans for the question filter, query embedding, vector search, answer generation and each judge call, so that the trace covers retrieval, generation and judging.
41. As the researcher, I want spans to use OpenInference span kinds and attribute names, so that Phoenix displays models, tokens and retrieved documents properly.
42. As the researcher, I want the vector search span to list the retrieved chunk IDs and scores, so that I can see what retrieval returned without opening the run file.
43. As the researcher, I want spans to carry model, token counts, latency, cost and cache hit or miss, so that I can find slow or expensive calls.
44. As the researcher, I want cache replays to carry no token counts, so that Phoenix doesn't charge for calls that were never made.
45. As the researcher, I want Claude's prompt token count to include cache reads and writes, so that Phoenix's cost matches what was billed.
46. As the researcher, I want Voyage cost and our own per-call cost recorded as attributes, so that the trace and the run file agree on cost.
47. As the researcher, I want the question text on the root span, so that I can find a question's trace.
48. As the researcher, I want prompt and answer text left out of spans unless I switch them on, so that traces stay small.
49. As the researcher, I want API keys and request headers never written to a span, so that a trace never leaks a secret.
50. As the researcher, I want judge spans to sit under their question even though judging runs in threads, so that judge cost is traceable to a question.
51. As the researcher, I want tracing completely off when `PHOENIX_COLLECTOR_ENDPOINT` is unset, so that ordinary runs make no extra network calls.
52. As the researcher, I want an eval run to finish quickly even if Phoenix is down, so that tracing can never stall a run.
53. As the researcher, I want tests never to send traces, even when Phoenix is running and the variable is set in my shell, so that test spans don't pollute real traces.
54. As the researcher, I want tracing to change no score and no run file, so that a traced run and an untraced run are interchangeable.

### Phoenix and local services

55. As the researcher, I want Phoenix started by `docker compose up -d` with a pinned image, so that it runs like Postgres and Neo4j.
56. As the researcher, I want traces kept across `docker compose down`, so that I don't lose them on restart.
57. As the researcher, I want Phoenix's telemetry and external resources off, so that the UI sends nothing to third parties.
58. As the researcher, I want Phoenix, Postgres and Neo4j reachable only from my own machine, so that nobody on a shared network can reach them.

## Implementation Decisions

### Gate

- A new module `eval.gate` with a CLI: `python -m eval.gate` runs the gate; `--update-baseline` rewrites the gate baseline from the current scores. Exit codes follow `eval.run`: 0 pass, 2 bad input, 3 the gate could not run, plus a distinct code for "scores dropped".
- The gate opens the vector arm with a snapshot query embedder. The arm already takes any `QueryEmbedder`; the snapshot embedder serves vectors from the snapshot by model and question text hash, and raises if a gated question's vector is missing. It never falls back to Voyage.
- The vector arm gains a retrieval-only method returning the retrieved chunks, the question filter, the per-company coverage and the timings. `run()` calls it before writing the answer, so the gate and `eval.run` share one search path. It needs no answer model, so the gate constructs no Anthropic client.
- Scores reuse the existing functions: recall@k from the eval metrics module, and wrong-evidence hits with an empty cited set. The gate's wrong-evidence rate is therefore the ranked-above-gold part of the eval's rate.
- Gated numbers: recall@5, recall@10 and wrong-evidence rate for each of lookup, local, multi_hop and global, and pooled over all four. Recall and precision@k are not both gated, because precision moves exactly with recall at fixed k.
- Comparison: a recall below the baseline fails; a wrong-evidence rate above the baseline fails. Equality passes. Values are compared after rounding to a fixed number of decimal places, so float formatting can't cause a false failure; the rounding is far finer than one question's worth (1/82).
- Question selection: the agent-drafted set's `dev` split, answerable classes only. The gate has no option to select `test`, and the snapshot holds no query vectors for `test` questions.
- Output: a table per class (before, after, delta) printed to stdout and appended to `GITHUB_STEP_SUMMARY` when that variable is set. The header line labels the questions "agent-drafted questions".
- Gate baseline file: JSON at `benchmarks/gate_baseline.json` holding the gated numbers, n per class, the commit, the snapshot SHA-256, the question set hash, the embedding model and k. The gate fails as bad input when the snapshot hash, question set hash, model or k differs from the baseline's. The `--update-baseline` path writes the file atomically and prints the diff against the old file.
- The first gate baseline is created in this step and checked by a test against the committed run `2026-10-05-vector-dev-96e8c69.json`: recall must match exactly per class; the wrong-evidence rate must match the run's per-question hits whose `above_gold` is true.

### Snapshot

- One gzipped JSON Lines file under the test fixtures, holding the filings rows (metadata and parsed text), the chunks rows, the `chunk_embeddings` rows for `voyage-4-large`, and the query vectors for the 82 gated questions keyed by model and question text hash. Estimated 10–12 MB.
- Built by the existing fixture builder script from the local database and the local query embedding cache, in a stable order, so a rebuild with unchanged data gives a byte-identical file.
- A loader applies the schema and inserts the rows through the existing store functions where they exist, so the schema's CHECKs (chunk ID format, text hash) run on the snapshot as on a real load. It returns the snapshot's SHA-256.
- No schema change.

### CI

- `ci.yml` keeps the `lint-and-test` job and adds a `quality-gate` job with the same pgvector service image. The gate job loads the snapshot and runs `python -m eval.gate`.
- Triggers become `pull_request` and `push` to `main` only. No `paths` filters.
- A GitHub ruleset on `main` requires the `quality-gate` check (and `lint-and-test`), with no bypass actors. It is created with `gh api` only after the user approves that specific change in chat.

### Tracing

- New dependencies (approved): `opentelemetry-sdk==1.45.0`, `opentelemetry-exporter-otlp-proto-http==1.45.0`, `openinference-semantic-conventions==0.1.41`. `arize-phoenix-otel` is not used.
- A small tracing module builds a `TracerProvider` with a `BatchSpanProcessor` and an OTLP HTTP exporter (2-second timeout) when `PHOENIX_COLLECTOR_ENDPOINT` is set, and nothing when it is not. It never calls `trace.set_tracer_provider`. The eval CLI shuts the provider down explicitly before exiting.
- The vector arm and the judge runner take an optional tracer. With none, they create no spans.
- Span layout per question: a CHAIN root span holding the question text; a CHAIN span for the question filter; an EMBEDDING span for the query embedding; a RETRIEVER span for the search, with `retrieval.documents.{i}.document.id` and `.score`; an LLM span for answer generation; for each judged metric, an EVALUATOR span with an LLM span per judge call.
- LLM spans carry `llm.model_name`, the provider and the token counts. Cache replays carry no `llm.token_count.*`. Claude's `llm.token_count.prompt` is uncached input plus cache reads plus cache writes, with the cache details in their own attributes. Our own per-call cost and the Voyage cost go in project-namespaced attributes, since Phoenix ignores cost attributes and has no Voyage price.
- Prompt and answer text go on spans only when a setting (an environment variable) turns it on. API keys and request headers are never attributes.
- The judge runner submits each task to its thread pool through `contextvars.copy_context().run`, so judge spans keep their parent. Scores and the run file don't change.

### Docker Compose

- A `phoenix` service: `arizephoenix/phoenix:20.19.0`, ports 6006 (UI and OTLP HTTP) and 4317 bound to `127.0.0.1`, a named volume at the Phoenix working directory, `PHOENIX_TELEMETRY_ENABLED=false`, `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false`, and a healthcheck that works in its distroless image (the research marks the python-based healthcheck unverified; confirm it or drop it).
- Postgres and Neo4j ports also bind to `127.0.0.1`.
- `.env.example` gains `PHOENIX_COLLECTOR_ENDPOINT` (commented out, so tracing stays off by default) and the text-capture setting.

## Testing Decisions

A good test here checks what the gate, the snapshot or a trace does from outside: the exit code, the printed comparison, the rows that come back, the spans recorded. It does not check private helpers or call order. Tests stay offline; database tests use the throwaway schema per session and skip without `DATABASE_URL`, as now.

- **Gate (seam 1, `python -m eval.gate` through its `main`).** Load the snapshot, or a small snapshot built from the test fixture filings, into the test schema and run the gate:
  - passes against its own baseline;
  - fails with the class and number named when the baseline is raised by one question's worth;
  - fails when the arm runs with k = 1;
  - fails as bad input when the snapshot hash, question set hash, model or k differs;
  - gives identical numbers on two runs;
  - `--update-baseline` writes the file and a following run passes;
  - writes the job summary only when `GITHUB_STEP_SUMMARY` is set;
  - never constructs an answer model or reads an API key.
  Prior art: the CLI tests of `eval.run` and `eval.compare`, and the database tests for `eval.validate --db`.
- **First baseline matches the committed run.** A test reads both files and checks recall exactly and wrong evidence against the run's above-gold hits. It runs in CI, since both files are committed.
- **Vector arm.** The retrieval-only method returns the same chunks `run()` used, checked with the existing fake embedder and fake answer model. Prior art: the vector arm tests.
- **Snapshot (seam 3).** Building from a database and loading into an empty one gives identical rows; a changed byte fails the hash check; the committed snapshot matches the local database when one is present and the test skips otherwise. Prior art: the `corpus_filings.json` check against `data/parsed`.
- **Tracing (seam 2).** Each test builds its own `TracerProvider` with an in-memory exporter and passes a tracer in. One question run through the fake answer model and fake judges produces the expected span names, kinds, parent links and attributes; a cache replay has no token counts; text attributes appear only with the setting on; no attribute holds a key. With no tracer, no spans are recorded. Judge spans started in worker threads have the question's root span as an ancestor. Prior art: the judge runner tests and the shared judge fakes.
- **Off by default.** An autouse fixture removes `PHOENIX_COLLECTOR_ENDPOINT` and `OTEL_EXPORTER_OTLP_*` for every test. A test checks that the tracing module builds no provider when the variable is unset.
- Deterministic logic (comparison, baseline file, snapshot round trip, span attributes) is written test-first, each test shown failing before the code.

## Out of Scope

- Gating answer or judge scores, and any live eval in CI. A manually triggered live eval may come later (ADR-0003).
- `benchmarks/results.md` (deferred in OPEN-DECISIONS).
- Gating any arm other than `vector`; graph arms join the gate when they exist.
- Running the `test` split anywhere.
- Changing the eval set, the answer prompt, k or any retrieval setting.
- Tracing outside the eval path: ingest, embed and the spot check CLI.
- Phoenix's own eval, dataset or experiment features.
- Git LFS.

## Further Notes

- The RUNBOOK asks for "~40 questions, for speed". The gate runs 82 because offline retrieval is fast; this is recorded in ADR-0003.
- The k=1 PR is opened from its own branch, must show a red `quality-gate` check, and is closed without merging. Its link goes in the BUILD-LOG entry.
- Once the ruleset is on, direct pushes to `main` are rejected. Step 6's own tickets merge through PRs once the gate job exists.
- If pgvector returns slightly different floats on CI's Linux runner than on macOS, the determinism and first-baseline tests will show it; the rounding before comparison is there for that case, and ties already break by chunk ID.
- The snapshot adds 10–12 MB to git history per rebuild; rebuild only when chunks or embeddings change.
