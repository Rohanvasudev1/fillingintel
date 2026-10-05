# 07: Judged metrics

**What to build:** the harness adds judged scores from `claude-opus-5-5`, following ticket 01's research: Ragas faithfulness and answer relevancy, citation support, and decline and not-found correctness.

**Blocked by:** 01, 05

**Status:** ready-for-agent

- [ ] Adds `ragas==0.4.3`. No DeepEval
- [ ] A project judge class subclasses Ragas's instructor base LLM and calls Claude structured outputs at effort `medium`, sending no sampling settings. It counts tokens for cost. One live check confirms effort and output format work together, as the research note asks
- [ ] An embedder wrapper lets Ragas answer relevancy use the Voyage client
- [ ] Judge responses go through the same response cache as answers, with the run number (1 to 3) in the cache key so the 3 judge runs stay independent
- [ ] `RAGAS_DO_NOT_TRACK=true` is set, and a test imports ragas with sockets blocked and fails on any download attempt
- [ ] Each judged metric runs 3 times. Results show the mean and spread, labelled "uncalibrated"
- [ ] Decline and not-found correctness reported in the decline and unanswerable rows
- [ ] Header records the Ragas version and the judge model ID and effort
- [ ] Tests use fake judges. pytest stays offline
- [ ] Reviewed with `mattpocock-skills:code-review`, every finding fixed or explained
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass
