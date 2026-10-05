# 09: Switch the judge to gpt-6-luna

**What to build:** `eval.run --arm vector` judges every answer with `gpt-6-luna` through OpenAI's Responses API instead of `claude-opus-5-5`, and the Opus judge path is gone. Ragas faithfulness, answer relevancy, citation support and decline/not-found correctness all score through one OpenAI judge class behind the existing `Judge` seam and response cache. The judge tests replay recorded `gpt-6-luna` responses offline. Source: the 2026-10-05 amendment in the Step 5 spec and docs/research/openai-luna-judge.md.

**Blocked by:** None (can start immediately; tickets 01–07 are done)

**Status:** done

- [x] `openai==3.3.0` is a direct dependency; the lock file changes no other versions (user-approved 2026-10-05)
- [x] The OpenAI judge class subclasses Ragas's `InstructorBaseRagasLLM` and sends a strict `json_schema` text format, `reasoning.effort`, `max_output_tokens` 25,000 and `store=false`, with no `temperature`, `top_p` or `seed` (request-shape test, written failing first)
- [x] Reply fields keep their declared order in the strict schema, so reasons come before verdicts (test)
- [x] A refusal, an `incomplete` reply or JSON that fails the schema raises the judge-reply error and is recorded against that one metric; API, network and cache failures raise the judge-run error and stop the run (tests)
- [x] OpenAI judge cache keys include `"provider": "openai"`; Anthropic answer cache keys and the recorded answer fixtures are unchanged (test)
- [x] `openai_cost` bills cached input at the cached rate and reasoning tokens as output, checked against hand-computed values; dated price rows for `gpt-6-luna` and `gpt-6-sol` with OpenAI's pricing URL
- [x] The run's usage totals report reasoning tokens separately
- [x] The judge model, effort, output cap, provider and openai SDK version are pinned in config and recorded in the results header
- [x] `eval.run` checks `OPENAI_API_KEY` at start, refuses to start when `OPENAI_BASE_URL` is set, and never prints the key; `.env.example` lists an empty `OPENAI_API_KEY`
- [x] A test imports `openai` with sockets blocked
- [x] The capture script records judge run 1 on the same three recorded answers with `gpt-6-luna`; the new fixtures replace the Opus ones, with keys and organisation IDs removed; the replay test passes offline on them. This is the first live call: confirm openai 3.3.0 works with `gpt-6-luna` and note luna's reasoning-token use
- [x] The Claude judge class, its tests, its fixtures and any unused Opus price row are deleted; the Anthropic answer model is untouched
- [x] CLAUDE.md's Step 5 model decision names `gpt-6-luna` as the judge
- [x] `mattpocock-skills:code-review` findings fixed or explained; `uv run ruff check .` and `uv run --env-file .env pytest` pass
- [x] BUILD-LOG entry, including the corrected ticket 07 cost (about $25 per graded dev run with Opus, not $10–15)
