# 01: Research how Ragas and DeepEval use Claude as the judge

**What to build:** a cited research note, written with `mattpocock-skills:research`, that tells ticket 07 exactly how to run Ragas faithfulness and answer relevancy, and DeepEval faithfulness, with `claude-opus-5-5` as the judge model. The note covers the adapter each library needs, any extra package, how to pin library and model versions, how each library reports token usage for cost, and whether DeepEval's faithfulness adds anything beside Ragas's.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Note saved in docs/research/, citing primary sources: library docs, source code or release notes, with versions
- [ ] States the exact Ragas and DeepEval versions to pin and the Claude adapter for each
- [ ] Lists any package beyond anthropic, ragas and deepeval. If there is one, the user approves it before ticket 07
- [ ] Recommends keeping or dropping DeepEval, with the reason
- [ ] Says whether either library makes network calls at import, which matters for offline pytest
