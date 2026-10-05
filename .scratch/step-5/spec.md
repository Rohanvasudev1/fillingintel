# Step 5: vector baseline and eval harness

Status: ready-for-agent
Sources: RUNBOOK Step 5, docs/RESEARCH-PLAN.md, and the grilling session of 2026-10-04, whose decisions are in CLAUDE.md Decisions, ADR-0001 and ADR-0002.

## Problem Statement

Postgres holds the chunked corpus and there are 140 agent-drafted eval records, but nothing answers a question and nothing scores an answer. Without a scored control arm there is no "before" number, so we can't show whether a later arm helps or hurts. The research plan fixes the metrics, the split rules and the hypotheses, but no code produces those metrics yet.

## Solution

Build the control arm and the harness that scores it.

- The `vector` arm embeds every chunk with `voyage-4-large`, applies a question filter parsed from the question text, retrieves the top 10 chunks by exact search, and has `claude-sonnet-5-5` write an answer in which every sentence cites a retrieved chunk.
- `python -m eval.run --arm vector` runs the arm on the `dev` split. It scores retrieval from chunk IDs and scores answers with LLM judges. It writes one results file with cells keyed by arm and class, bootstrap intervals, full provenance and the label "agent-drafted questions".

## User Stories

1. As the researcher, I want every chunk embedded once by a command I run, so that retrieval works offline afterwards.
2. As the researcher, I want the embed command to fail if any chunk exceeds the model's input limit, so that no chunk is silently truncated.
3. As the researcher, I want each stored embedding to carry the model name, dimensions and a hash of the embedded text, so that I can detect a stale or mismatched vector.
4. As the researcher, I want rerunning the embed command to skip chunks whose vector is current, so that reruns cost nothing.
5. As the researcher, I want preamble chunks embedded like any other, so that retrieval can reach the two dev gold chunks in Intel's 10-K preamble.
6. As the researcher, I want embeddings stored apart from the approved `chunks` table, so that the chunk schema and its mismatch check stay unchanged.
7. As the researcher, I want a second embedding model to fit beside the first without a migration, so that a later ablation is cheap.
8. As the researcher, I want the question filter derived from the question text alone, so that no arm sees an eval record's labels.
9. As the researcher, I want "fiscal 2025", "FY2025" and "fiscal year 2025" to filter to that fiscal year's 10-K and 10-Qs, so that period phrasing works for all three companies.
10. As the researcher, I want "the first quarter of fiscal 2026" to filter to that quarter's 10-Q only, so that quarter questions don't pull other quarters.
11. As the researcher, I want "Q3 2025" and "the third quarter of 2025" to filter by period for AMD and Intel but not for NVIDIA, so that NVIDIA's offset fiscal year never produces a wrong filter.
12. As the researcher, I want a bare year such as "August 2025" to set no period filter, so that event dates aren't mistaken for reporting periods.
13. As the researcher, I want "10-K" or "10-Q" in a question to restrict the form type, so that form-specific questions retrieve from the right filings.
14. As the researcher, I want "latest" or "most recent" next to a form name to mean that company's newest filing of that form in the corpus, so that "latest 10-K" questions resolve deterministically.
15. As the researcher, I want questions naming several companies or periods to filter to their union, so that comparison questions keep all their evidence.
16. As the researcher, I want the parser to set no filter when unsure, so that a missed filter costs precision rather than all the recall.
17. As the researcher, I want the filter parser's exact-match rate against the labels reported on dev, so that its accuracy is visible.
18. As the researcher, I want a filter-excluded-gold count reported, and required to be 0 on dev before the baseline run, so that the filter never silently caps recall.
19. As the researcher, I want the vector arm to retrieve the top 10 chunks by exact cosine similarity within the filter, so that the control has no approximate-search misses.
20. As the researcher, I want questions embedded with the `query` input type and chunks with `document`, as Voyage recommends for retrieval.
21. As the researcher, I want query embeddings cached on disk by model and text hash, so that rerunning the same questions makes no embedding calls.
22. As the researcher, I want all 10 retrieved chunks passed to the answer model, so that retrieval, not truncation, decides what the model sees.
23. As the researcher, I want every answer sentence to cite a chunk ID in brackets, so that each claim traces to a character span, as invariant 3 requires.
24. As the researcher, I want sentences without a citation, or citing a chunk that wasn't retrieved, dropped and counted, so that uncited claims never reach the answer.
25. As the researcher, I want the answer model to decline investment-advice questions, so that invariant 6 holds before the router exists.
26. As the researcher, I want the answer model to say when the retrieved filings don't contain the answer, so that unanswerable questions get a fair score.
27. As the researcher, I want the answer prompt stored as a versioned file whose edits create a new version, so that every result traces to the exact prompt.
28. As the researcher, I want the answer model's ID and effort pinned and every API response cached, so that reruns reproduce answers even though the models accept no sampling settings.
29. As the researcher, I want `python -m eval.run --arm vector` to score `dev` by default, so that `test` stays unseen until the final benchmark.
30. As the researcher, I want scoring `test` to need an explicit `--final` flag, so that it can't happen by accident.
31. As the researcher, I want the run to stop with a clear message when an API key is missing, so that a long run doesn't fail halfway.
32. As the researcher, I want context recall and precision at 5 and 10 computed from chunk IDs, so that retrieval metrics are exact and need no LLM.
33. As the researcher, I want a wrong-evidence rate per arm and class, counting questions where a hard negative ranks above a gold chunk or gets cited, so that I can test H3.
34. As the researcher, I want the wrong-evidence rate broken down by hard-negative label, so that I can tell period confusion apart from peer or same-filing confusion.
35. As the researcher, I want a deterministic structural citation check, that each cited ID exists and was retrieved, so that I can measure the citation guarantee.
36. As the researcher, I want an LLM to judge whether each cited chunk supports its sentence, with the label "uncalibrated", so that no one overstates the support rate.
37. As the researcher, I want faithfulness and answer relevancy from Ragas, so that the RUNBOOK's judged metrics exist.
38. As the researcher, I want one uncached repeat run of the dev questions in the baseline, so that I can see how much scores move between runs.
39. As the researcher, I want a judge to check whether answers to decline and unanswerable records declined or said the evidence is absent, with these records in their own rows, so that they don't distort the four main classes.
40. As the researcher, I want each judge to run 3 times, with the mean and spread reported, so that I can see judge noise.
41. As the researcher, I want every judged number labelled "uncalibrated" until judge calibration is done, so that no one reads it as validated.
42. As the researcher, I want a bootstrap 95% interval on every cell with a fixed seed, so that small classes show their uncertainty.
43. As the researcher, I want the label "directional" on classes with fewer than 10 records in the scored split, as the research plan requires.
44. As the researcher, I want p50 and p95 latency per arm and class, split into retrieval and generation, so that I can test H5 later.
45. As the researcher, I want cost per query from the token counts each API reports and a dated price table, so that I can trace every cost figure.
46. As the researcher, I want one results file per run, named by date, arm, split and commit, so that runs never overwrite each other.
47. As the researcher, I want the results header to record the commit with `+dirty`, every model ID, the prompt version and hash, k, chunker_version, the eval set's SHA-256, the split, the seed, both models' effort levels and the Ragas version, so that I can trace any number to its cause, as invariant 2 requires.
48. As the researcher, I want the header to label the run "agent-drafted questions", so that no result passes as human-written or human-verified.
49. As the researcher, I want a per-question record of the filter, retrieved IDs, answer, citations, dropped sentences, latency and cost, so that failures can be read one by one.
50. As the researcher, I want each multi-company question's record to show which named companies got no retrieved chunks, so that I can see what a plain top 10 costs.
51. As the researcher, I want git to hold only the run I call the baseline, with other runs ignored, so that the repo has one "before" number.
52. As a reader of the benchmark, I want the baseline's numbers reported with the commit and config, so that I can reproduce them.
53. As a future graph-arm implementer, I want the harness to take an arm by name through one interface, so that new arms plug in without harness changes.
54. As a future graph-arm implementer, I want the question filter parser shared, so that every arm gets the same filters.
55. As CI, I want every test to run offline on recorded API responses, so that the suite needs no keys or network.
56. As the researcher, I want API keys never written to logs or results files, so that invariant 7 holds.

## Implementation Decisions

- **Embedding model.** `voyage-4-large` with 1024 dimensions, per ADR-0002. Calls go over `httpx` with truncation off, the `document` input type for chunks and `query` for questions. No Voyage SDK.
- **Schema change, approved.** A new `chunk_embeddings` table keyed on `(chunk_id, model)` references `chunks`. It holds the dimensions, a SHA-256 of the embedded text, the token count the API reported, the vector and a timestamp. It has no ANN index. `apply_schema()` checks its live columns as it does for the other tables. The `chunks` table doesn't change.
- **Embed command.** A new network command, `python -m ingest.embed`, run by the user like `ingest.corpus`. It embeds missing or stale chunks in batches within Voyage's per-request limit. If any chunk exceeds the model's input limit, it fails before making any API call.
- **Question filter parser.** One deterministic module shared by all arms. Input: question text. Output: a filter of companies, periods and form types, each possibly empty. Its rules are those in ADR-0001: hard filter, explicit period phrases only, calendar quarters for AMD and Intel only, "latest" bound to a form name, union for several companies or periods, no filter when unsure. To resolve "latest", the caller passes in the corpus's filing list, so the parser never queries the database.
- **Interfaces for the network.** Three small interfaces: embedder, answer model and judge. The real versions are Voyage over `httpx`, the Anthropic SDK, and Ragas with a project judge class. Tests use fakes that replay recorded responses.
- **Vector arm.** It takes the question, the filter parser, the embedder, the answer model and a database connection. It retrieves the top 10 by exact cosine similarity over `chunk_embeddings` joined to `chunks`, within the filter, and reads chunk text through `resolve()`. It returns the filter used, the retrieved chunk IDs with scores, the final answer, its citations, the dropped sentences, latency per stage and token usage.
- **Citation enforcement.** A deterministic step splits the answer into sentences. A sentence survives only if it cites at least one `[chunk_id]` and every ID it cites is among the retrieved chunks. The output keeps and counts the dropped sentences.
- **Answer prompt.** A versioned prompt file, version 1. It demands a citation per sentence, declines buy/hold/sell questions and price targets, and says when the retrieved filings lack the evidence. Any edit creates a new version, and no one edits an existing version.
- **Models.** `claude-sonnet-5-5` writes answers at effort `high` and `claude-opus-5-5` judges at effort `medium` (judge superseded by the 2026-10-05 amendment below: `gpt-6-luna`). Both reject non-default sampling settings, so sampling stays at the defaults. Config pins IDs and efforts and each run records them. A response cache keyed by model, effort and prompt hash makes reruns reproducible.
- **Harness command.** `python -m eval.run --arm <name> [--split dev|test] [--final]`. `dev` is the default, and the command refuses `test` without `--final`. It checks the API keys at start. It reads `eval/agent_drafted_set.jsonl`. It reads `eval/eval_set.jsonl` only if that file has records, and reports them in their own column.
- **Metrics.**
  - From chunk IDs: context recall and precision at 5 and 10, the wrong-evidence rate overall and per hard-negative label, and structural citation validity.
  - Judged: Ragas faithfulness and answer relevancy, citation support, and decline/not-found correctness. Each judged metric runs 3 times. The report shows the mean and spread, with the label "uncalibrated".
  - Operational: p50 and p95 latency per stage, and cost per query.
- **Statistics.** Every cell gets a bootstrap 95% interval with a fixed, recorded seed. Classes with fewer than 10 records in the scored split get the label "directional".
- **Filter parser report.** Each run reports exact match against the labels and filter-excluded-gold. The baseline run needs filter-excluded-gold to be 0 on dev.
- **Results file.** Each run writes one JSON file named `{date}-{arm}-{split}-{commit}` in a benchmarks runs directory. It has a provenance header, cells keyed by arm and class, the filter parser report and per-question records. Git ignores the run directory except for the committed baseline.
- **Configuration.** `VOYAGE_API_KEY` and `ANTHROPIC_API_KEY` come from the environment and appear empty in `.env.example`. The price table lives in config with its date. Logs and results never contain secrets.
- **Dependencies, approved.** `anthropic` and `ragas==0.4.3`. LangGraph waits for Step 13. The user dropped DeepEval on 2026-10-05 after the ticket 01 research. Ragas runs through a project judge class on Claude's structured outputs and an embedder wrapper around the Voyage client, with `RAGAS_DO_NOT_TRACK=true`.

## Testing Decisions

- **Good tests check behaviour at a seam, not internals.** Each test calls a public entry point with real inputs, such as real chunk IDs, real eval records and recorded API responses, and checks the outputs a user would see.
- **Test-first for all deterministic logic.** Following the CLAUDE.md workflow, each test fails visibly before its code exists.
- **Four seams.**
  1. *Question filter parser.* Pure function tests: every phrasing class in ADR-0001, NVIDIA's offset fiscal year, event-date years, "latest", unions, and a test over the dev records asserting filter-excluded-gold = 0.
  2. *Embedding load.* `ingest.embed` against the throwaway per-session test schema with a fake embedder: one row per chunk, the stored text hash and token count, skipped reruns, and failure on an oversized chunk before any call.
  3. *Vector arm.* Against the test schema with a fake embedder and a fake answer model: the filter narrows retrieval, the top 10 come back in similarity order, citation enforcement drops and counts the right sentences, and decline and not-found answers pass through.
  4. *Harness.* `eval.run` main with a fake arm and fake judges: (arm, class) cells, ID-based metrics against hand-computed values, the wrong-evidence rate, bootstrap intervals reproducible under the seed, "directional" marking, the provenance header, the dev default and the `--final` guard.
- **Offline.** No test touches the network, and pytest-socket stays as it is. The recorded responses are real API responses, captured once and stored as test data with keys removed.
- **Prior art.** `tests/test_store.py` and `tests/test_load.py` use the throwaway database schema. `tests/test_eval_tools.py` and `tests/test_eval_db.py` test eval tooling against real records. `tests/test_tokenizer_offline.py` checks the offline guarantee.
- **Thresholds.** No one loosens a test or check to make it pass, per invariant 5.

## Out of Scope

- Graph arms, the router and LangGraph, which belong to Steps 11 to 13.
- The CI eval gate and Phoenix tracing, which belong to Step 6.
- Judge calibration against hand scores. It's required before the final benchmark, not here.
- Scoring the `test` split, except through an explicit `--final` run, which isn't part of this step.
- A per-company quota for multi-company questions, contextual chunk headers, rerankers and a second embedding model. Each would be a separately proposed, labelled ablation.
- The FinRank external check. It's an appendix follow-up after the baseline exists.
- The open parser leads: the missing headcount and capex table in Intel's FY2025 10-K, and the duplicated table header in NVIDIA's Q3 FY2026 10-Q.

## Further Notes

- **Instructions given in chat.** The user asked for a recommendation on every decision. All grilling answers were "as recommended", across three rounds and the seam check.
- **Stop condition, from the RUNBOOK.** `python -m eval.run --arm vector` produces a scored results file on dev. Write down the multi-hop number, which is the "before".
- **Honesty labels.** Every result carries the label "agent-drafted questions" and every judged number "uncalibrated". The baseline run goes into git with its commit and config, per invariant 2.
- **Known risk.** Hard negatives labelled `same_company_other_period` may contain the correct figure, because a later 10-K restates prior-year comparatives. If that inflates the wrong-evidence rate, flag it. Don't relabel records, per invariant 1.

---

## Amendment 2026-10-05: the judge moves to gpt-6-luna

Status: ready-for-agent
Sources: the grilling session of 2026-10-05 in chat, and docs/research/openai-luna-judge.md. This amendment replaces every mention of `claude-opus-5-5` as the judge above. Answers are still written by `claude-sonnet-5-5`. No ADR: the user asked for an amendment line only.

### Problem Statement

A graded dev run with `claude-opus-5-5` as the judge costs about $25 (recomputed from the recorded judge responses; the ticket 07 BUILD-LOG figure of $10 to $15 understated it). The baseline, the uncached repeat run and every later arm each pay that again. The user wants a cheaper judge before the baseline runs, without giving up a check that the cheaper judge scores like a strong one.

### Solution

- `gpt-6-luna` (OpenAI) becomes the judge for Ragas faithfulness, answer relevancy, citation support and decline/not-found correctness. Estimated cost per dev run is about $0.62, a lower bound until luna's reasoning-token use is measured.
- The Opus judge path is deleted, not kept behind config.
- Before the baseline, a spot check judges 10 fixed dev questions with `gpt-6-luna` at effort `medium` and `high` and with `gpt-6-sol` at effort `medium` as the reference judge. The pass rule is fixed now, before any result is seen:
  - yes/no verdicts (citation support, decline correctness, not-found correctness): luna agrees with sol on at least 85% of verdicts;
  - Ragas faithfulness and answer relevancy: the mean absolute gap between luna and sol is at most 0.10 per metric.
- The cheaper luna effort that passes both rules judges the baseline. If neither passes, `gpt-6-sol` at `medium` judges it (about $12.40 per dev run).
- The spot check is not calibration. Every judged number keeps the label "uncalibrated", and calibration against hand scores (Cohen's kappa of at least 0.6, RESEARCH-PLAN) is still required before the final benchmark.

### User Stories

1. As the researcher, I want the judge to be `gpt-6-luna`, so that a graded dev run costs under a dollar instead of about $25.
2. As the researcher, I want the Opus judge code removed, so that there is one judge path to maintain and no expensive path can be selected by mistake.
3. As the researcher, I want the judge model and effort pinned in config and recorded in every results header, so that a reader knows which model produced each judged number.
4. As the researcher, I want the judge called through OpenAI's Responses API with a strict JSON schema, so that every reply parses into the reply models Ragas and the project's judges expect.
5. As the researcher, I want no sampling settings sent to the judge, so that requests are not rejected and reruns rely on the response cache for reproducibility.
6. As the researcher, I want `store=false` on every judge request, so that OpenAI keeps no stored copy of our prompts and replies beyond its abuse-monitoring window.
7. As the researcher, I want reply fields to keep their declared order in the schema, so that the judge still writes its reason before its verdict.
8. As the researcher, I want a refusal, an incomplete reply or JSON that fails the schema to raise the existing judge-reply error, so that the scorer records the failure against that one metric as it does today.
9. As the researcher, I want an API, network or cache failure to stop the run with the existing judge-run error, so that no partial results file is written.
10. As the researcher, I want every judge request to go through the existing response cache, with the provider in the cache key, so that reruns replay the same judge replies and OpenAI keys can never collide with Anthropic ones.
11. As the researcher, I want the cached answers from `claude-sonnet-5-5` reused unchanged, so that switching the judge costs no new answer calls.
12. As the researcher, I want the three judge runs per question kept, with mean and spread reported, so that run-to-run variance stays visible.
13. As the researcher, I want an output-token cap of 25,000 at first, so that reasoning does not truncate verdicts before we know luna's real usage.
14. As the researcher, I want the cap lowered only after the spot check shows real usage, recorded as a config change, so that cached replies are invalidated knowingly.
15. As the researcher, I want an OpenAI cost function that bills cached input at the cached rate and reasoning tokens as output, so that reported judge cost matches OpenAI's billing.
16. As the researcher, I want dated price-table rows for `gpt-6-luna` and `gpt-6-sol` with OpenAI's pricing URL, so that every cost figure can be traced.
17. As the researcher, I want reasoning tokens reported separately in the run's usage totals, so that I can see how much of the judge cost is thinking.
18. As the researcher, I want `eval.run` to check `OPENAI_API_KEY` at start, alongside the Anthropic and Voyage keys, so that a missing key fails before any answer is written.
19. As the researcher, I want the OpenAI client to set its API address itself and to refuse to start when `OPENAI_BASE_URL` is set, so that the key can never be sent to another host.
20. As the researcher, I want the key never printed in errors, logs or the client's repr, per invariant 7.
21. As the researcher, I want `.env.example` to list an empty `OPENAI_API_KEY`, so that a new checkout shows every key the harness needs.
22. As the researcher, I want `openai` pinned as a direct dependency at the version already locked, so that the lock file changes no versions.
23. As the researcher, I want `import openai` to stay offline under pytest-socket, so that CI still makes no network calls.
24. As the researcher, I want the judge tests to replay real recorded `gpt-6-luna` responses instead of the Opus ones, so that parsing and scoring are tested on genuine API output.
25. As the researcher, I want the capture script pointed at `gpt-6-luna`, so that the fixtures can be re-recorded with one command.
26. As the researcher, I want a spot-check command that judges 10 fixed dev questions with each configured judge, so that luna is compared with a stronger reference before the baseline.
27. As the researcher, I want the 10 questions chosen deterministically from the dev split and spread across classes, so that the check is repeatable and not hand-picked.
28. As the researcher, I want the pass thresholds written in code and in this spec before the check runs, so that the bar cannot move after the results are seen.
29. As the researcher, I want the spot check to report verdict agreement and mean score gap per metric, per luna effort, with pass or fail, so that the choice of judge and effort follows from the numbers.
30. As the researcher, I want the spot-check result and the chosen judge recorded in the BUILD-LOG with the commit, labelled "spot check, not calibration", so that no one reads it as validation.
31. As the researcher, I want the fallback to `gpt-6-sol` to be a config change only, so that failing the spot check does not need new code.
32. As the researcher, I want the BUILD-LOG to correct the ticket 07 cost estimate, so that the cost argument for the switch rests on accurate numbers.
33. As the researcher, I want CLAUDE.md's Step 5 model decision updated to name the new judge, so that future sessions do not rebuild the Opus path.

### Implementation Decisions

- **One judge class for OpenAI.** It subclasses Ragas's `InstructorBaseRagasLLM` like the Claude judge it replaces, keeps the run number, repeat counter and call log, and calls the Responses API with a strict `json_schema` text format built by the openai SDK's strict-schema conversion, `reasoning.effort`, `max_output_tokens` and `store=false`. It sends no `temperature`, `top_p` or `seed`. Ragas's built-in OpenAI route is not used: it misreads the model version and sends sampling settings (research note, section 4).
- **The `Judge` protocol, `JudgeSpec` and the runner stay as they are.** The Claude-specific judge set is renamed to a provider-neutral name and wired to the OpenAI backend and the Voyage embedder. The judge spec's required environment variables become the OpenAI and Voyage keys; `eval.run` still requires the Anthropic key for answers.
- **An OpenAI backend** implements the existing answer-model protocol (`complete(request) -> ApiResponse`) so the response cache wraps it unchanged. A separate reply parser reads OpenAI's response shape into the existing token-usage and reply types, mapping `incomplete` status and refusals to unusable stops.
- **Cache key.** OpenAI judge requests add `"provider": "openai"` to the hashed key. The Anthropic answer request key is unchanged, so cached answers and the Anthropic answer fixtures stay valid. The cache's `{model}/{effort}` folder layout is unchanged.
- **Config.** Judge model `gpt-6-luna`, effort `medium` until the spot check decides, `max_output_tokens` 25,000, three runs. The results header records model, effort, cap, provider and the openai SDK version.
- **Pricing.** An `openai_cost` function: OpenAI's input count includes cached tokens, so cached tokens are subtracted and billed at the cached rate; reasoning tokens are part of output. Price-table rows for `gpt-6-luna` and `gpt-6-sol`, dated, with the source URL.
- **Spot check.** A pure comparison function takes two judges' per-question judged results on the same questions and returns, per metric, verdict agreement or mean absolute gap and a pass flag against the fixed thresholds. A small `eval.spotcheck` command selects 10 dev questions by hash across classes, reuses the cached answers, judges them with each configured judge and effort, and prints and writes the comparison. Its output file is not committed; the BUILD-LOG records the numbers and the commit.
- **Deletion.** The Claude judge class, its tests, the Opus judge fixtures and the Opus price row (if no longer used) are removed. The Anthropic answer model is untouched.
- **Dependency.** `openai==3.3.0` added as a direct dependency (user-approved 2026-10-05); it is already in the lock through ragas.

### Testing Decisions

- **Good tests check behaviour at a seam.** Same rule as above: real inputs, recorded API output, outputs a user would see. Test-first for every deterministic piece.
- **Seam 1, the `Judge` seam with replay.** The existing replay test loads recorded `gpt-6-luna` responses (judge run 1 on the same three recorded answers) and checks the real Ragas and project scores offline. Unit tests cover the request shape (strict schema, `store=false`, effort, no sampling settings, provider in the cache key), the reply parser (refusal, incomplete, bad JSON raise the judge-reply error) and `openai_cost` against hand-computed values.
- **Seam 2, the spot-check comparison.** Pure-function tests with hand-built judged results: perfect agreement, agreement exactly at 85% and just under, gaps at 0.10 and just over, missing scores, and mismatched question sets refused. The question selection is tested for determinism and class spread against the dev records.
- **Seam 3, the `eval.run` harness.** Existing tests updated: the OpenAI key is required, a set `OPENAI_BASE_URL` stops the run, the header names the judge model and provider.
- **Offline.** No test reaches the network; a test imports `openai` with sockets blocked. Recorded responses are stored with keys and organisation IDs removed.
- **Prior art.** The current judge replay and judge class tests, the `eval.compare` tests and `tests/test_ragas_offline.py`.

### Out of Scope

- Keeping the Opus judge selectable.
- Batch or Flex processing.
- Calibration against hand scores.
- Changing the answer model, the answer prompt or the judge prompts.
- Changing judge prompts to suit luna; if luna fails the spot check, the fallback is `gpt-6-sol`, not prompt tuning.

### Further Notes

- **Instructions given in chat.** The user chose `gpt-6-luna` over `gpt-5.6-luna` because it is cheaper, asked to delete the Opus path because of its cost, asked for an amendment line rather than an ADR, accepted all eight technical defaults from the research note, and asked that the switch fit in one ticket if possible. All other answers were "as recommended".
- **Unverified facts to confirm on the first live call:** that openai 3.3.0 works with `gpt-6-luna`, and luna's reasoning-token volume.
- **Disk space.** The machine had 2.8 GB free on 2026-10-05. Recording fixtures and running the spot check need little, but check before the baseline.
- **Spot-check rules settled before the run (ticket 10 review, user decisions 2026-10-05, all as recommended).**
  - No cached answers existed, so the spot check writes the 10 Sonnet answers into the shared response cache, and the baseline replays them. "No new answer calls" now reads as "no answer is paid for twice".
  - Each judge scores every question 3 times, as in a graded run. Agreement stays per metric, so the decline and not-found rows (one question each) get 3 verdicts, not 1.
  - A metric also fails when more than 15% of its items have a value from only one judge. Items both judges skipped do not count.
  - Citation-support agreement is counted per sentence verdict. Verdicts are paired by run number. Score gaps are taken between each judge's per-question mean over its runs.
