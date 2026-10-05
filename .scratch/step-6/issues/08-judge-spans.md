# 08: Judge spans linked to their question

**What to build:** judge calls appear in each question's trace. For each judged metric, an EVALUATOR span holds one LLM span per judge call, with model, effort and token counts under the same cache-replay and cost rules as ticket 07. The judge runner submits each task to its thread pool through `contextvars.copy_context().run`, so spans started in worker threads keep the question's root span as an ancestor. Scores and run files don't change. See docs/research/phoenix-tracing.md section 4.

**Blocked by:** 07

**Status:** ready-for-agent

- [ ] The judge runner takes an optional tracer; with none, it records no spans and behaves exactly as before.
- [ ] A test with the existing judge fakes and an in-memory exporter shows each judge LLM span under its EVALUATOR span, under the question's root span, with judging running on the thread pool.
- [ ] Judge cache replays carry no `llm.token_count.*`; fresh calls carry the counts the OpenAI response reported.
- [ ] Existing judge runner, replay and scoring tests pass unchanged.
- [ ] Demo: with Phoenix up, a fully cached traced dev run (`eval.run --arm vector`) completes with no paid calls, shows judge spans under each question in Phoenix, and `python -m eval.compare` against the committed baseline run reports no change in any cell.
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass.
