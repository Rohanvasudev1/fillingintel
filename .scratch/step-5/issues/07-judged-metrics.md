# 07: Judged metrics

**What to build:** the harness adds judged scores from `claude-opus-5-5`, following ticket 01's research: Ragas faithfulness and answer relevancy, DeepEval faithfulness as a second opinion, citation support, and decline and not-found correctness.

**Blocked by:** 01, 05

**Status:** ready-for-agent

- [ ] Adds `ragas` and `deepeval` at the versions ticket 01 pinned, plus any extra package the user approved
- [ ] Each judged metric runs 3 times. Results show the mean and spread, labelled "uncalibrated"
- [ ] Large Ragas and DeepEval faithfulness disagreements are flagged per question
- [ ] Decline and not-found correctness reported in the decline and unanswerable rows
- [ ] Header records the Ragas and DeepEval versions and the judge model ID
- [ ] Tests use fake judges. pytest stays offline
- [ ] Reviewed with `mattpocock-skills:code-review`, every finding fixed or explained
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass
