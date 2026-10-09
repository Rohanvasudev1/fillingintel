# 03: `python -m extract.run`

**What to build:** one command that extracts a whole 10-K by accession number and leaves a committed run report. It reads the chunks and their text from Postgres, checks its settings, prints a cost estimate and refuses over the cap, runs `extract_filing()` with 4 calls at a time, writes the candidates file and the run report, and exits with the codes the other commands use (spec: user stories 1–13, 58–62).

**Blocked by:** 02 (`extract_filing()`)

**Status:** ready-for-agent

- [ ] Stops at start, exit 3, when `ANTHROPIC_API_KEY` or `DATABASE_URL` is missing, when `ANTHROPIC_BASE_URL` is set, or when Postgres is unreachable. Bad arguments exit 2 (argparse).
- [ ] The cost estimate allows for Claude's tokenizer counting about 30% more than cl100k; an estimate over `--max-cost` (default $10) exits 3 before any call.
- [ ] A new dated price table with Sonnet 5.5 cache reads at $0.10 per MTok (source: the research note's reading of the pricing page); the old table is kept and past costs are unchanged. The report records which table and date it used.
- [ ] Calls run 4 at a time with contextvars copied, so tracing spans keep their parent. Each chunk call is an LLM span with model, tokens, cost, cache hit and chunk ID; chunk and reply text only under `FILINGINTEL_TRACE_TEXT=true`; tracing off unless `PHOENIX_COLLECTOR_ENDPOINT` is set.
- [ ] Candidates go to the gitignored extract folder under data, named by accession and run number; the run report goes to `benchmarks/extraction/`, with commit, prompt version and hash, model, effort, ontology version, chunk count, candidates, rejections by reason with examples, flags, conflicts, failed chunk IDs, tokens (input, cache read, cache write, output) and cost.
- [ ] Exit 0 when every chunk completed and `check_batch()` found nothing; exit 4 on any failed chunk or any `check_batch()` violation, and the report says the run is incomplete.
- [ ] A rerun with an unchanged prompt makes zero model calls (tested with a counting fake model behind the cache).
- [ ] `extract/` is added to `git_state()`'s +dirty list.
- [ ] Tests first and shown failing (`/tdd`). Code review with `mattpocock-skills:code-review` (SQL axis). `uv run ruff check .` and `uv run --env-file .env pytest` pass, and CI is green.
