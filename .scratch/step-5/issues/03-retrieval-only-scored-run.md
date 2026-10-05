# 03: A retrieval-only scored run

**What to build:** the first end-to-end path. `python -m eval.run --arm vector` embeds each dev question (input type `query`, cached on disk by model and text hash), retrieves the top 10 chunks by exact cosine similarity with no filter, scores context recall and precision at 5 and 10 from chunk IDs, and writes a results file. No answers yet.

**Blocked by:** 02

**Status:** ready-for-agent

- [x] The harness takes an arm by name through one interface, so later arms plug in without harness changes
- [x] `dev` is the default split. `test` is refused without `--final`
- [x] Missing API keys stop the run at start with a clear message
- [x] Reads eval/agent_drafted_set.jsonl. Reads eval/eval_set.jsonl only if it has records, reporting them in their own column
- [x] Results file named {date}-{arm}-{split}-{commit} in a benchmarks runs directory, which git ignores
- [x] Header records commit with +dirty, model IDs, k, chunker_version, the eval set's SHA-256, split, seed and the label "agent-drafted questions"
- [x] Cells keyed by arm and class. Decline and unanswerable records get their own rows
- [x] Per-question records hold retrieved chunk IDs with scores and retrieval latency
- [x] Metric tests compare against hand-computed values on real eval records. Harness tests use a fake arm
- [x] Reviewed with `mattpocock-skills:code-review`, every finding fixed or explained
- [x] `uv run ruff check .` and `uv run --env-file .env pytest` pass
