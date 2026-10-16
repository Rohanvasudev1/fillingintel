# Step 13: Router and the benchmark

Status: ready-for-agent
Sources: RUNBOOK Step 13 and the Step 11–12 outcomes, docs/RESEARCH-PLAN.md (H1–H6 and the amendment of 2026-10-16), docs/OPEN-DECISIONS.md (the Step 11–13 items settled at this grilling), docs/ARCHITECTURE.md, ADR-0001, ADR-0003, ADR-0007, ADR-0008, and the grilling session of 2026-10-16. Every round was answered "as recommended", with three changes from the user: judge calibration is B + C (30 answers the user scores with claims split by an agent, plus a corruption test) instead of 60 hand-scored answers; spend is capped at $10 (from about $17.60), which drops the `graph_global` dev answer run outside global questions, makes `graph_global` retrieval-only on 33 `test` questions, and halves the corruption test; and the seven test seams were approved as proposed. External research: docs/research/router-langgraph-and-judge-calibration.md (LangGraph, published routers, Haiku 5.5 settings, judge calibration statistics and sample sizes, a claim-level scoring protocol). Vocabulary: CONTEXT.md (router, predicted class, routing map, oracle router, router accuracy, calibration, corruption test, arm, control arm, judge, split, agent-drafted).

## Problem Statement

Every benchmark number so far is on `dev`, the questions every tuning choice was made on: hop count, Cypher prompt revision, N for global search. The project's claim to rigour rests on a final table scored once on the 47 held-out `test` questions, and that table does not exist.

Four pieces are missing:
- **No router.** RESEARCH-PLAN's H6 fixed a class-to-arm map before any run, and invariant 6 says the router declines investment advice, but nothing classifies a question or dispatches it. Every graph arm has since lost to `vector` on dev in its own class (pooled recall@10 0.789 and 0.778 against 0.890; global 0.458 against 0.800), so a router following the fixed map is expected to lose too, and that has to be measured, not assumed.
- **Judged numbers are uncalibrated.** Faithfulness, relevancy and citation support come from gpt-6-luna and have never been checked against a human. H4 is judged on relevancy. RESEARCH-PLAN made calibration a condition of the final table, and its "Cohen's kappa ≥ 0.6" does not fit 0–1 scores.
- **No results table.** ARCHITECTURE says CI regenerates `benchmarks/results.md`, but CI makes no network calls and cannot run the matrix. Numbers live in BUILD-LOG prose.
- **H5 cannot be tested.** The harness reports latency per stage, never per question, so "p95 at least 2x `vector`" has no total to compare.

The user is cost-sensitive and capped Step 13 at $10.

## Solution

A router classifies each question with claude-haiku-5-5 into `lookup`, `local`, `multi_hop`, `global` or `decline`. A `decline` returns the fixed decline sentence with no retrieval; any other class goes to the arm a routing map names. It is a plain dispatch function registered with the harness like an arm (`eval.run --arm router --map …`), though it is not one. Two maps are scored: the pre-registered map from H6 (the headline router row) and a dev-best map chosen by a fixed rule from dev recall@10 (labelled "tuned on dev"). An oracle router, given the gold class, separates misclassification from the map's choice. Router effort and at most one prompt revision are chosen on dev with a classification-only command.

The harness gains a per-question total latency, a `--max-cost` refusal and a `--retrieval-only` mode. After the dev work, every arm, prompt, effort and map is frozen, and `test` is scored once with `--final`: `vector`, `graph_local` and `graph_traversal` in full, `graph_global` in full on global, decline and unanswerable questions and retrieval-only on the rest, and the router under both maps.

Calibration is B + C. B: the user scores 30 dev answers through `eval.calibrate`, marking agent-split claims supported or not, rating relevancy on a rubric and answering yes or no for citation support and decline or not-found; agreement is computed per score type. C: `eval.corrupt` plants four kinds of known error in 30 real dev answers and reports how often the judge catches each.

`python -m eval.results_table` builds `benchmarks/results.md` offline from committed run files, and CI rebuilds it and fails on any difference. The table leads with `test`, prints H1–H6 verdicts from the pre-registered rules, and carries every label. The stop condition is that table, committed, with every cell explained in BUILD-LOG, including the losses.

## User Stories

1. As the project owner, I want a router that gives every question a predicted class, so that the benchmark can test whether routing beats one fixed arm.
2. As the project owner, I want the router to predict only `lookup`, `local`, `multi_hop`, `global` or `decline`, so that it never claims to know a question is unanswerable before searching.
3. As the project owner, I want a `decline` prediction to return the fixed decline sentence with no retrieval and no answer call, so that investment-advice questions cost almost nothing and invariant 6 holds at the router.
4. As the project owner, I want the answer prompt's own decline kept, so that a misrouted advice question is still declined.
5. As the project owner, I want the classifier on claude-haiku-5-5 with structured output, a short reason before an enum, so that classification is cheap and every choice has a recorded reason.
6. As the project owner, I want the classifier prompt pinned by SHA-256 with a version string, so that the run header says exactly which prompt routed the questions.
7. As the project owner, I want the few-shot examples invented and never taken from the eval set, so that router accuracy is not inflated by seen questions.
8. As the project owner, I want the router to see only the question text, so that it cannot read gold labels, as ADR-0001 requires of question filters.
9. As the project owner, I want effort chosen among `low`, `medium` and `high` on dev by accuracy, ties to the lower effort, so that latency is not spent where it buys nothing.
10. As the project owner, I want at most one prompt revision, driven by the dev confusion matrix, with both versions reported, so that the router is not fitted question by question.
11. As the project owner, I want a classification-only command for dev, so that the effort sweep and the v1/v2 comparison cost cents and run no arm.
12. As the project owner, I want the router run through the same harness as the arms, so that its results file, metrics, judges and costs are computed the same way.
13. As the project owner, I want the router's results to say clearly that it is not an arm, so that the glossary and the table agree.
14. As the project owner, I want the pre-registered map as the headline router row, so that H6 is tested as fixed before any run.
15. As the project owner, I want a dev-best map as a second row labelled "tuned on dev", so that readers see what a router built from the evidence does.
16. As the project owner, I want the dev-best rule fixed before the dev runs it reads, so that it cannot be bent to a result.
17. As the project owner, I want the dev-best rule to take the arm with the highest dev recall@10 per class and send statistical ties to the cheaper arm, so that the choice rests on deterministic metrics, not uncalibrated judges.
18. As the project owner, I want an oracle router reported beside the router, so that I can tell how much of the router's loss is misclassification and how much is the map.
19. As the project owner, I want the oracle assembled from cached answers, so that it costs nothing.
20. As the project owner, I want router accuracy over answerable and decline questions with a Wilson 95% interval, so that a small sample is read with its uncertainty.
21. As the project owner, I want a confusion matrix on dev and on `test`, so that I can see which classes the router mixes up.
22. As the project owner, I want a decline question counted correct only when the router itself declined it, so that the answer prompt's fallback decline does not hide a routing miss.
23. As the project owner, I want false declines listed, so that over-refusal is visible.
24. As the project owner, I want unanswerable questions kept out of accuracy and given their own `not_found` share under the router, so that a class with no gold arm is still measured.
25. As the project owner, I want router accuracy labelled "against agent-drafted class labels", so that nobody reads the gold classes as human labels.
26. As the project owner, I want H6 judged on dev as written (at least 80%, every decline declined), so that the pre-registered test is applied unchanged.
27. As the project owner, I want each routing decision recorded per question (predicted class, reason, map, chosen arm, effort, prompt version), so that every cell can be traced to the decisions behind it.
28. As the project owner, I want a `route` span with the classifier call as its child, so that routing shows in Phoenix like every other step.
29. As the project owner, I want routing cost and latency as their own stage, so that the router row's cost and latency include classification.
30. As the project owner, I want the router run to replay arm answers and judge replies from the response cache, so that the router rows cost only classification where the arm already answered.
31. As the project owner, I want an end-to-end `total_ms` on every question record, so that H5 is tested on what a user waits for.
32. As the project owner, I want p50 and p95 of `total_ms` per cell with intervals, so that latency is reported like every other metric.
33. As the project owner, I want questions with replayed calls flagged in latency, so that cached replays are not mistaken for live speed.
34. As the project owner, I want `eval.run --max-cost` to refuse a run whose estimate exceeds the cap, so that an overrun stops before it starts.
35. As the project owner, I want each arm to supply its own estimate, so that the refusal prices the requests that arm would really send.
36. As the project owner, I want a `--retrieval-only` mode in `eval.run` that writes a normal results file with retrieval metrics and no answers or judges, so that `graph_global` gets per-question recall on dev and `test` at almost no cost.
37. As the project owner, I want retrieval-only cells marked "retrieval only" in every table, so that missing judged cells are not read as zeros.
38. As the project owner, I want the dev `graph_global` retrieval-only run to feed the dev-best rule, so that its paired intervals use per-question numbers.
39. As the project owner, I want every arm, prompt, effort, map and threshold frozen before `test`, so that `test` is scored once.
40. As the project owner, I want a `test` run that stops partway to be resumable with the same commit and config, so that an API error does not count as a second look.
41. As the project owner, I want a post-`test` bug fix allowed only when the bug does not depend on which answers came out well, with both runs committed and footnoted, so that fixes stay honest.
42. As the project owner, I want any change of prompt, k, map or threshold after `test` to be a new dev-only row, so that `test` stays clean.
43. As the project owner, I want `graph_global` run in full on `test` only on global, decline and unanswerable questions, so that Step 13 fits the $10 cap.
44. As the project owner, I want every paid run to show its estimate first, so that I can stop it before spending.
45. As the project owner, I want Step 13's spend summed from committed run files and recorded test replies, so that the $10 cap is checked against real spend.
46. As the project owner, I want to score 30 dev answers myself through `eval.calibrate`, so that judged columns can be checked against a human.
47. As the project owner, I want the 30 answers drawn by a fixed seed, stratified by arm and class, from dev only, so that `test` stays unseen and the draw is reproducible.
48. As the project owner, I want each answer's claims split by claude-sonnet-5-5 in a call separate from the judge, so that I am not checking the judge's own split.
49. As the project owner, I want to mark each claim supported or not against the retrieved chunks shown with their IDs, so that my faithfulness score uses the same definition as Ragas.
50. As the project owner, I want to flag a claim the split got wrong as "not a claim" or add one it missed, so that a bad split does not force a wrong score.
51. As the project owner, I want a 1–5 relevancy rubric with written anchors, so that my ratings are consistent.
52. As the project owner, I want yes or no per kept sentence for citation support, and yes or no for decline and not-found correctness, so that binary judge outputs are checked one for one.
53. As the project owner, I want the judge's scores hidden until I have entered mine, so that I am not anchored by them.
54. As the project owner, I want the tool to log time per answer and ask me to look again at fast answers, so that a rubber-stamp pass is caught, as in the extraction review.
55. As the project owner, I want to stop and resume across sittings, so that an hour of scoring need not be one session.
56. As the project owner, I want the tool to refuse non-interactive input and agents never to run it, so that only my judgments count as hand scores.
57. As the project owner, I want 10 answers re-scored about a week later, so that my own consistency is reported.
58. As the project owner, I want Cohen's kappa for citation support, Spearman's rho with mean absolute error and signed bias for faithfulness and relevancy, and percent agreement with the disagreement count for decline and not-found, so that each score type gets a statistic that fits it.
59. As the project owner, I want pass or fail read on the point estimate against 0.6 with the 95% interval printed, so that the pre-registered bar holds and its looseness at n = 30 is visible.
60. As the project owner, I want a column that fails calibration left out of the headline table, so that RESEARCH-PLAN's rule is applied.
61. As the project owner, I want passing columns labelled "calibrated, n = 30, claims split by an agent", so that the label says exactly what was done.
62. As the project owner, I want judge agreement with itself across its 3 runs reported, so that the judge's own noise sets the floor for human agreement.
63. As the project owner, I want a corruption test that plants a changed number, a swapped fiscal period, a citation to the wrong chunk and an unsupported sentence in 30 real dev answers, so that the judge is tested where the truth is known by construction.
64. As the project owner, I want the corruptions made by code with a fixed seed and no LLM, so that the test is deterministic and free apart from judging.
65. As the project owner, I want catch rates per error type for faithfulness and citation support, with the uncorrupted answer's score beside each, so that a catch means the score fell.
66. As the project owner, I want the corruption test labelled as not calibration, so that the two checks are not confused.
67. As the project owner, I want `results.md` built by a command from committed run files, so that no number in it is typed by hand.
68. As the project owner, I want CI to rebuild `results.md` and fail on any difference, so that the committed table always matches the committed runs.
69. As the project owner, I want the `test` table first, so that the headline is the held-out result.
70. As the project owner, I want rows for every arm and both router rows by class, plus decline and unanswerable rows, so that the full matrix is visible.
71. As the project owner, I want columns for n, recall@5, recall@10, precision@10, wrong evidence, faithfulness, relevancy, citation support, p50/p95 total latency and cost, with intervals, so that every metric RESEARCH-PLAN names is there.
72. As the project owner, I want paired differences against `vector` with "directional" marked where n < 10, so that small classes are not over-read.
73. As the project owner, I want H1–H6 verdicts computed from the pre-registered thresholds with each rule printed, so that verdicts cannot be softened in prose.
74. As the project owner, I want the router section to hold accuracy, the confusion matrix and the oracle, so that routing is read in one place.
75. As the project owner, I want Cypher validity, ER precision and recall, extraction accuracy and violation rate, and persistence precision and recall in the table with commits and labels, so that the numbers most projects don't publish sit next to the benchmark.
76. As the project owner, I want dev tables last, labelled as tuning data, so that they are available but not mistaken for the result.
77. As the project owner, I want every label kept (agent-drafted questions, agent-labelled, tuned on dev, uncalibrated or calibrated, retrieval only, directional), so that the table cannot be read as more than it is.
78. As the project owner, I want the load run name written onto `:GraphMeta` and read by the graph arms, so that the run header names the graph actually queried.
79. As the project owner, I want a second write to the query-embedding cache to keep the existing vector, so that concurrent judge threads cannot change a cached embedding.
80. As the project owner, I want a stricter provenance count that traces each citation through the map point that cited it, reported beside the existing check, so that global provenance is shown at both strengths.
81. As the project owner, I want the `graph_global` chunk-handoff ablation scored on dev retrieval only, with the arm unchanged, so that the Step 12 question is answered at no cost.
82. As the project owner, I want a BUILD-LOG entry that explains every cell, including the losses, so that the stop condition is met in writing.
83. As a reader of the README, I want the router's loss, if it loses, reported as plainly as a win, so that the benchmark is credible.
84. As a reviewer, I want each run file to name its commit, config and prompt hashes, so that any number can be reproduced.

## Implementation Decisions

**Router (`retrieve/`).** A deep module `router` exposes one opener that the arm registry lists under the name `router`, taking a map name (`pre_registered` or `dev_best`). It implements the existing `Arm.run(question) → ArmResult` contract by:
1. Classifying the question with claude-haiku-5-5 through the existing Anthropic client and response cache, with structured output `{reason, predicted_class}` (enum of the five labels, reason first), `prompts/route/v1.md` pinned by SHA-256 (version string `route/v1@<hash8>` covering system prompt, user template and output schema), class descriptions and 1–2 invented few-shot examples per label. The question text is the only input.
2. For `decline`: returning an `ArmResult` with no retrieved chunks, the fixed decline sentence (the existing `DECLINE_TEXT`, status `declined`), no answer call, zero answer cost.
3. Otherwise calling the inner arm the map names and returning its result with the routing record attached.

Inner arms are opened lazily on first use and closed with the router. A reply that is cut off, refused or off the schema is an `ArmError` (the question fails, as other arms' API failures do); there is no default class. `ArmResult` gains an optional `routing` record: predicted class, reason, map name, chosen arm (or `decline`), effort, prompt version and hash, and the classifier's cost and latency. `ArmConfig` records the router model, effort, prompt and map, and the inner arms' configs. The run header names each inner arm's provenance as that arm's own run would.

Effort is set in config from the dev sweep. The two maps live in code as frozen tables; the dev-best table is written once by the results module's rule (below) and committed with the run that chose it, so the router reads a file, not a computation.

**Spans and stages.** A `route` CHAIN span holds the predicted class, chosen arm and map; the classifier call is an LLM child span with model, tokens and cost (prompt text only under `FILINGINTEL_TRACE_TEXT`). The inner arm's spans nest under the question span as before. A `routing` stage carries latency and cost; the router row reports `routing` plus the inner arm's stages.

**`python -m eval.route`** (network, classification only). Runs the classifier over the dev records (answerable and decline; unanswerable recorded but left out of accuracy) at a given effort and prompt version, prints an estimate first and refuses above `--max-cost`, and writes `benchmarks/router/route-run{N}.json`: commit, model, effort, prompt version and hash, per-question predicted class and reason, accuracy with Wilson 95% interval, the confusion matrix, false declines and the unanswerable share per predicted class. Sweep: `low`, `medium`, `high` with `route/v1`; the most accurate wins, ties to the lower. Then, if the confusion matrix shows a pattern worth a revision, one `route/v2` at the chosen effort; the more accurate version is frozen. All run files are committed. `test` is refused without `--final`, and `--final` is used only through `eval.run`.

**Harness (`eval.run`, approved).**
- `total_ms` per question: wall time around the whole `arm.run` call, routing included. Cells gain p50 and p95 of `total_ms` with bootstrap intervals; a question with any replayed call is flagged and the cell says how many. H5 reads `total_ms`.
- `--max-cost <usd>`: before opening the arm, the harness asks the arm spec for an estimate over the selected records (answer, retrieval and judge calls) and refuses above the cap with exit 2. Each arm spec supplies an estimator, reusing `retrieve.cypher_estimate` and `retrieve.global_estimate` where they exist; `vector` and `graph_local` estimate from token counts with cl100k_base and the dev mean output; the router estimates classification plus the inner arms' answers for questions without a cached answer. The estimate is printed with or without the flag.
- `--retrieval-only`: runs retrieval only, writes a normal results file whose header records the mode, whose cells hold retrieval metrics and retrieval latency, and whose answer, judged and cost fields are absent. No answer or judge call is made. Allowed on any arm and split (with `--final` for `test`).
- The router is selected with `--arm router --map pre_registered|dev_best`; `--map` is refused for other arms. File names include the map: `{date}-router-{map}-{split}-{commit}.json`.

**Dev runs (in order).**
1. `graph_global --retrieval-only` on the 82 answerable dev questions ($0, cached embeddings).
2. The effort sweep and optional revision with `eval.route`.
3. The dev-best map, chosen by the results module's rule from the four dev arm files (`graph_global`'s retrieval-only one for lookup, local, multi_hop; its full one for global) and written to `benchmarks/router/dev-best-map.json` with the paired intervals that decided it.
4. `eval.run --arm router --map pre_registered` and `--map dev_best` on all dev records. Answers replay from the cache where the chosen arm already answered; `graph_global` answers live for the few non-global questions routed to it.

**Freeze, then `test` once.** A frozen record, `benchmarks/router/frozen.json`, pins the router prompt version and hash, effort, both maps' hashes, and each arm's config hash and prompt versions, and refuses to write on a dirty tree. `eval.run --final` refuses when the frozen record is missing or differs from the live config. `test` runs: `vector`, `graph_local`, `graph_traversal` full on all 47; `graph_global` full with `--classes global,decline,unanswerable` (14) and `--retrieval-only --classes lookup,local,multi_hop` (33); router under both maps on all 47. A run that stops partway is resumed with the same commit and config; its cache makes completed questions replay. After `test`, the rules in the Decisions entry apply: no prompt, k, map or threshold change; a bug fix and rerun only for bugs independent of which answers came out well, with both runs committed and a footnote.

**Results module (`eval/`).** A deep module `results_table` takes the committed run files and companion reports and returns the markdown of `benchmarks/results.md`; `python -m eval.results_table` writes it, and `--check` exits 4 when the committed file differs (the CI step). It reads only committed files and makes no network or database call. Contents in order:
1. The `test` table: rows `vector`, `graph_local`, `graph_traversal`, `graph_global`, router (pre-registered), router (dev-best), grouped by class, then decline and unanswerable rows (decline and not-found correctness). Columns: n, recall@5, recall@10, precision@10, wrong-evidence rate, faithfulness, answer relevancy, citation support, p50 and p95 `total_ms`, cost per question, each with its 95% interval. Cells from a retrieval-only file print "retrieval only" in judged, latency-total and cost columns. Judged columns carry the calibration label for their metric, or are left out when calibration failed.
2. Paired differences against `vector` on `test` (the same computation as `eval.compare`, reused), "directional" where n < 10, counted only when the interval excludes 0.
3. H1–H6 verdicts: each rule from RESEARCH-PLAN printed with the numbers it reads and "supported", "not supported" or "directional". H6 reads the dev router run files.
4. Router: accuracy with Wilson interval and the confusion matrix for dev and `test`; false declines; unanswerable `not_found` share; the oracle row (gold class through the pre-registered map, assembled from the arm files' per-question records, with retrieval-only arms marked); the effort sweep and prompt versions.
5. Other measured numbers, each with its commit, file and label: Cypher validity (first pass, after repair, non-empty, fallback), ER precision and recall (rounds 1 and 2), extraction accuracy and violation rate, persistence precision and recall, calibration results, corruption catch rates, the provenance counts, the chunk-handoff ablation.
6. The dev tables, labelled "tuning data".

A header lists every input file with its SHA-256 and commit. The dev-best rule is a pure function in this module: per answerable class, the arm with the highest mean recall@10; any arm whose paired difference against it has an interval including 0 ties, and ties go to the lowest dev cost per question (retrieval-only arms take their dev full-run cost where one exists, else are priced by the estimator). Run files under `benchmarks/runs/` that `results_table` reads are force-added to git, as before.

**CI.** One step runs `python -m eval.results_table --check` in the lint-and-test job. It needs only the committed files. The gate job and its baseline are unchanged.

**Calibration (`eval/calibration/`).**
- Draw: a pure function picks 30 dev answers by fixed seed from the committed dev run files, stratified by arm and class (answers with at least one kept sentence for faithfulness; decline and unanswerable records for behaviour), writing `benchmarks/calibration/draw.json` once (write-once, like resolution draws).
- Claims: `python -m eval.calibration.claims` (network) sends each drawn answer's kept sentences to claude-sonnet-5-5 at effort `high` with structured output `{claims: [{sentence_index, text}]}` under `prompts/claims/v1.md` (pinned), through the response cache, estimate first, and writes `benchmarks/calibration/claims.jsonl`. This prompt is separate from the judge's.
- Scoring: `python -m eval.calibrate --reviewer "<name>"` (user only; refuses non-interactive input, like `eval.review`). Per answer it shows the question, the kept sentences, the retrieved chunks with IDs, and the claims; the user marks each claim supported, not supported or not a claim (and may add a missed claim), gives a reason for every "not supported", rates relevancy 1–5 on the rubric shown, answers citation support yes or no per kept sentence, and for decline and unanswerable records answers decline or not-found correctness. Judge scores are hidden until the answer's entries are saved, then shown. Time per answer is logged; under 15 s prompts "take another look?". Entries append to `benchmarks/calibration/scores-{reviewer}.jsonl` and the tool resumes at the first unscored answer. `--rescore 10` re-presents 10 drawn answers by seed, hiding the earlier entries, for intra-rater agreement.
- Statistics: `python -m eval.calibration.report` (offline) computes, against the judge's mean over its 3 runs: faithfulness (supported over supported plus not supported) and relevancy (rubric mapped to 0–1 by (r−1)/4) by Spearman's rho with a Fisher-z interval, mean absolute error and mean signed difference; citation support per kept sentence by Cohen's kappa against the judge's majority verdict over 3 runs (per-sentence verdicts read from the cached judge replies), with an interval bootstrapped by answer; decline and not-found by percent agreement and the disagreement count. It also reports judge-against-itself agreement across runs and, when present, the user's re-score agreement. Pass is the point estimate at or above 0.6 (rho or kappa). It writes `benchmarks/calibration/report.json`, which `results_table` reads for labels.

**Corruption test (`eval/corrupt`).** A pure module makes, from one real answer and its retrieved chunks, four corrupted copies with a fixed seed: a number in a kept sentence changed (the first number with a unit, scaled by a fixed factor); a fiscal period swapped for another period in the corpus; one citation replaced by a retrieved chunk the sentence does not draw on (the lowest-ranked retrieved chunk not cited); and one added sentence that states a fact absent from every retrieved chunk, built from a fixed template with the citation of an existing sentence. An answer where a corruption cannot apply skips that type and the skip is counted. `python -m eval.corrupt` (network) draws 30 dev answers by its own seed (disjoint from the calibration draw), judges the corrupted copies for faithfulness and citation support with the pinned gpt-6-luna judge, 3 runs, through the response cache, estimate first, and writes `benchmarks/calibration/corruption-run{N}.json`: per error type, n, the share where the score fell below the uncorrupted answer's (faithfulness) or the corrupted sentence's verdict was "not supported" (citation support), with Wilson intervals. Labelled "corruption test, not calibration".

**Graph and cache items.**
- `graph.load` writes the load run name onto `:GraphMeta` in the rebuild transaction; `validate_graph()` requires it; the graph arms read it from there for the run header, and refuse when it is missing. Ontology version and schema hash are unchanged, so no prompt hash moves.
- The query-embedding cache keeps an existing entry when a second write for the same key arrives (write to a temporary file, then link only when the key is absent).
- `graph.global_provenance` adds a strict count: a kept citation passes only when the map point it came from (the kept map point citing that chunk in the reduce input) belongs to a community whose members or member edges evidence the chunk. Both counts are written; the existing one stays the gate.
- `python -m eval.global_handoff` (offline, cached embeddings) scores, retrieval only on the 82 answerable dev questions, `graph_global` handing its top 10 chunks ranked by cosine across the selected communities, against the arm as built, and writes `benchmarks/graph/global-handoff-run1.json` labelled "ablation, dev".

**Spend.** Step 13 is capped at $10. `graph/step13_spend.py` (or the equivalent sum) adds the cost recorded in every Step 13 run file (route runs, router runs, `test` runs, claims, corruption) and the recorded test replies. Each paid command checks its estimate plus committed Step 13 spend against the cap. Planned: about $0.25 router sweep and runs, $0.50 dev `graph_global` answers for routed questions, $6.75 `test`, $0.10 claims, $0.60 corruption: about $8.20.

## Testing Decisions

A good test checks behaviour through the highest seam a caller uses, on real data: real dev questions and run files, real chunk IDs from the fixture filings, recorded model replies, and the `neo4j-test` and Postgres throwaway schemas. It never asserts on prompt wording, log text or internal helper calls. Deterministic logic is shown failing before the code is written. CI stays offline.

Seams (approved at the spec step):
1. **The router through `Arm.run`.** In-memory stand-in arms record which question they received; the classifier replays recorded Haiku replies to invented questions (captured by a script after its estimate is shown). Tests: each predicted class goes to the arm the map names; `decline` returns the fixed sentence with no retrieval and calls no arm; the routing record and `route` span hold class, reason, map and arm; routing cost and latency are a stage; a cut-off or off-schema reply is an `ArmError`; the dev-best map is read from its file and a missing file refuses at open; the request carries model, effort, schema and the pinned prompt hash. Prior art: `tests/test_graph_global_arm.py`, recorded replies.
2. **`eval.run`.** Tests: `total_ms` present, covering routing, and summarized per cell; replayed questions flagged; `--max-cost` refuses above the estimate with exit 2 and runs below it; `--retrieval-only` makes no answer or judge call and writes retrieval cells only; `--map` only with the router; `--final` refused without a matching frozen record. Prior art: `tests/test_eval_run.py`, `tests/test_eval_run_graph.py`.
3. **`eval.route`.** Tests use recorded classifier replies for a handful of dev questions, captured once and committed like other recorded replies. Tests: accuracy, Wilson interval and confusion matrix computed correctly; decline counted only when declined; unanswerable excluded; estimate and cap refusal; `test` refused. Prior art: `tests/test_global_n.py`.
4. **`eval.results_table`.** Inputs are the real committed dev run files and reports; `test` behaviour is tested by passing dev files in the `test` role. Tests: every row and column present with intervals; retrieval-only cells say so; failed calibration drops its column; paired differences equal `eval.compare`'s; each H verdict follows its printed rule on a constructed case near its threshold; the dev-best rule picks the highest recall@10 and sends ties to the cheaper arm; the oracle row uses gold classes; `--check` exits 4 on a one-character difference and 0 on a match; output is byte-identical across runs. Prior art: `tests/test_eval_compare.py`.
5. **`eval.calibrate`.** Scripted interactive input through the existing review fakes. Tests: refuses non-interactive input; judge scores hidden until entries are saved; resume starts at the first unscored answer; fast answers prompt a second look; "not supported" needs a reason; the re-score hides earlier entries. The draw is write-once and stratified; statistics are pure functions tested on small known cases (rho, kappa, MAE, signed bias, percent agreement) and bootstrap by answer. Prior art: `tests/test_eval_review.py`, `tests/review_fakes.py`.
6. **`eval.corrupt`.** The corruptors run on real dev answers and chunks: each produces exactly its one change, is deterministic for a seed, and skips when it cannot apply. Catch rates come from recorded judge replies for a few corrupted answers. Prior art: `tests/test_judge_runner.py`.
7. **Existing seams for the small items.** `:GraphMeta` load run written, validated and read (`tests/test_graph_load.py`, `tests/test_graph_validate.py` on `neo4j-test`); a second cache write keeps the first vector (`tests/test_query_cache.py`); the strict provenance count on a run with one citation from the wrong community (`tests/test_global_provenance.py`); the handoff ablation's ranking on cached embeddings (`tests/test_global_n.py`).

## Out of Scope

- LangGraph. The router is a plain dispatch function; LangGraph is reconsidered at the Step 13b grilling.
- Step 13b agentic arms and their tool-call cap.
- Path context in answers, path-level citations and the path-precision metric (OPEN-DECISIONS, after Step 13).
- Extending the quality gate to graph arms (a Neo4j snapshot in CI, a Cypher validity floor).
- Any change to the four arms, their prompts, k, N or hop count. The chunk-handoff ablation is reported and not adopted.
- `graph_global` judged cells on `test` lookup, local and multi_hop questions, and `graph_global` dev answers outside global questions, beyond those the router sends there.
- Full calibration on 60–100 answers, a second human scorer, and changing models (answers stay claude-sonnet-5-5, judges gpt-6-luna, embeddings voyage-4-large).
- README prose and the interpretation of the table (Step 14).
- New tickers, data sources or re-extraction.

## Further Notes

- Dev cost per question, from committed run files including judges: `vector` $0.038, `graph_local` $0.037, `graph_traversal` $0.041, `graph_global` $0.086. These, not guesses, drive the estimates; Step 12's only overrun went the other way ($1.81 spent against a $4.02 estimate).
- `test` holds 47 questions: 13 lookup, 13 local, 7 multi_hop, 5 global, 4 decline, 5 unanswerable. Every `test` class except lookup and local is under 10 and reported as directional.
- At n = 30, a rho of 0.6 has a 95% interval of about [0.31, 0.79]; the table prints it.
- The research found Adaptive-RAG's classifier at 54.5% accuracy still improved end-to-end results, so router accuracy and router benefit are reported separately.
- LangGraph 1.2.12 stays in the lock through ragas; nothing imports it.
- The 10-answer re-score comes about a week after scoring. Proposal for review: Step 13 closes without waiting for it, and the re-score is added to the calibration report and `results.md` in a later PR.
- Every number is on agent-drafted questions; `results.md` says so above the first table.
