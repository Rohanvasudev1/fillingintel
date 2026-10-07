# 08: Judge spans linked to their question

**What to build:** judge calls appear in each question's trace. For each judged metric, an EVALUATOR span holds one LLM span per judge call, with model, effort and token counts under the same cache-replay and cost rules as ticket 07. The judge runner submits each task to its thread pool through `contextvars.copy_context().run`, so spans started in worker threads keep the question's root span as an ancestor. Scores and run files don't change. See docs/research/phoenix-tracing.md section 4.

**Blocked by:** 07

**Status:** done (2026-10-07; PR #9 merged as 335f731)

**Note from ticket 07:** the root `answer_question` span in `eval/run.py` `_run_arm` wraps only `arm.run`; judging runs later in `_judge`, after every root span has ended. To put judge spans under their question, keep each question's span context (for example on the outcome) and start the EVALUATOR span with it as parent. Copying the context in the runner alone is not enough.

- [x] The judge runner takes an optional tracer; with none, it records no spans and behaves exactly as before.
- [x] A test with the existing judge fakes and an in-memory exporter shows each judge LLM span under its EVALUATOR span, under the question's root span, with judging running on the thread pool.
- [x] Judge cache replays carry no `llm.token_count.*`; fresh calls carry the counts the OpenAI response reported.
- [x] Existing judge runner, replay and scoring tests pass unchanged.
- [x] Demo: with Phoenix up, a fully cached traced dev run (`eval.run --arm vector`) completes with no paid calls, shows judge spans under each question in Phoenix, and `python -m eval.compare` against the committed baseline run reports no change in any cell.
- [x] `uv run ruff check .` and `uv run --env-file .env pytest` pass.

**Notes from the implementation:**
- "Optional tracer" means an optional `SpanRecorder`, as in ticket 07. `RagasJudges`, `OpenAIJudge` and `open_judges` take one, defaulting to none. `JudgeSpec.open` now takes `(cache, spans)` like `ArmSpec.open`, so the judge fakes in `tests/test_eval_run.py` and `tests/test_tracing_run.py` gained a `spans` parameter. The runner, replay and scoring tests are unchanged.
- `eval.run`'s `_run_arm` returns each question's root span context next to its outcome. `judge_all(..., parents=...)` runs each job through `contextvars.copy_context().run` and attaches that context, so the EVALUATOR spans start under the root after it has ended. The context stays out of `Outcome`, which is results-file data.
- Each EVALUATOR span is named after its metric and records `filingintel.judge.metric`, `filingintel.judge.run` and the score as JSON in `output.value`. A reply the judge cannot score sets the span's status to ERROR and puts the reason in `filingintel.judge.error`.
- Judge LLM spans (`judge_call`) map OpenAI usage as research section 3.3 says, plus `cache_write` and `total`. `input_tokens` already includes cached and written tokens, so the prompt count is `input_tokens` as reported. As for answers, a replay keeps `filingintel.cost_usd` at the recorded cost.
- Judge prompt text never goes on spans, even with `FILINGINTEL_TRACE_TEXT=true`. Whether it should is listed as a decision in the BUILD-LOG entry.
