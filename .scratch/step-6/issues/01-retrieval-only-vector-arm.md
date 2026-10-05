# 01: Retrieval-only method on the vector arm

**What to build:** a prefactor. The vector arm gains a method that searches without answering: given the question text, it returns the retrieved chunks with scores (best first), the question filter, the named companies with no retrieved chunk, and the embedding and search timings. `run()` calls this method and then writes the answer, so the quality gate (ticket 03) and `eval.run` share one search path. Behaviour of `run()` and of `eval.run` does not change.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] The retrieval-only method needs no answer model call, and a caller can use it without an answer model or `ANTHROPIC_API_KEY`.
- [ ] A test shows the retrieval-only method returns exactly the chunks, scores and filter that `run()` used for the same question (existing fake embedder and fake answer model).
- [ ] A test shows the method respects the arm's k (k = 1 returns one chunk).
- [ ] All existing vector arm and `eval.run` tests pass unchanged.
- [ ] Errors from embedding, the cache or the database are raised as `ArmError`, as in `run()`.
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass.
