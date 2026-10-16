# 03: The router, from build to freeze

**What to build:** `eval.run --arm router --map pre_registered|dev_best` classifies each question with claude-haiku-5-5, declines investment-advice questions with no retrieval, and hands the rest to the arm the routing map names. Effort and the prompt are chosen on dev with the classification-only `eval.route`. The router then runs on dev under both maps; `results.md` gains the router section (accuracy, confusion matrix, oracle router, H6); and everything is frozen so `test` can be scored once. Spec: `.scratch/step-13/spec.md` (Router; Spans and stages; `eval.route`; Harness, router bullet; Dev runs steps 2 and 4; Freeze). Vocabulary: CONTEXT.md (router, predicted class, routing map, oracle router, router accuracy).

**Blocked by:** 01, 02

**Status:** ready-for-agent

**Parallel sessions:** one ticket at a time (user, 2026-10-16). `neo4j-test` is shared: run the full `uv run --env-file .env pytest` only when no other session is running pytest, with dev Neo4j stopped. Symlink `data/` and `.env` from the main checkout; run API commands with `env -u ANTHROPIC_BASE_URL`. One branch and PR per ticket. Step 13 spend is capped at $10 in all, counting committed spend; show the estimate before any paid call.

- [ ] `prompts/route/v1.md`, pinned by SHA-256 (`route/v1@<hash8>` covering system prompt, user template and output schema): class descriptions and 1–2 invented few-shot examples per label, never eval questions. Structured output `{reason, predicted_class}` with the five labels.
- [ ] The router implements `Arm.run`: question text only in; `decline` returns the fixed decline sentence with status `declined`, no retrieval and no answer call; other classes call the mapped arm, opened lazily and closed with the router. A cut-off, refused or off-schema reply is an `ArmError`.
- [ ] `ArmResult` gains an optional routing record (predicted class, reason, map, chosen arm, effort, prompt version and hash, classifier cost and latency). `ArmConfig` and the run header record router model, effort, prompt and map, and each inner arm's provenance. File names carry the map. `--map` is refused for other arms; the dev-best map is read from its file, and a missing file refuses at open.
- [ ] A `route` CHAIN span with class, arm and map, the classifier as an LLM child span (prompt text only under `FILINGINTEL_TRACE_TEXT`); a `routing` stage with latency and cost. The router's estimator covers classification plus uncached inner answers.
- [ ] `python -m eval.route` (network): dev answerable and decline records, estimate first, `--max-cost`, `test` refused; writes `benchmarks/router/route-run{N}.json` with per-question class and reason, accuracy with Wilson 95% interval, confusion matrix, false declines and the unanswerable share per predicted class.
- [ ] Sweep `low`, `medium`, `high` with `route/v1`; most accurate wins, ties to the lower effort. At most one `route/v2`, driven by the confusion matrix; the more accurate version is frozen; both reported. All route runs committed.
- [ ] Router dev runs under both maps on all 93 dev questions, estimate first (about $0.50), committed.
- [ ] `results.md` router section: accuracy and confusion matrix (dev), false declines, unanswerable `not_found` share, the oracle router from cached per-question records, the sweep and prompt versions, and H6 judged on dev as written. Labelled "against agent-drafted class labels".
- [ ] `benchmarks/router/frozen.json` pins router prompt and effort, both maps' hashes and each arm's config hash and prompt versions; refuses a dirty tree. `eval.run --final` refuses without a matching frozen record.
- [ ] Recorded Haiku replies (invented questions for the router tests, a handful of dev questions for `eval.route`) captured by a script after its estimate. Tests, seen failing first: through `Arm.run` with in-memory stand-in arms (dispatch per map, decline, routing record, spans, stage, errors, request shape); `eval.route` metrics; `eval.run` `--map` and `--final` refusals; the router section on constructed records.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green; BUILD-LOG entry with spend.
