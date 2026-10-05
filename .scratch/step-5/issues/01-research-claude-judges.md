# 01: Research how Ragas and DeepEval use Claude as the judge

**What to build:** a cited research note, written with `mattpocock-skills:research`, that tells ticket 07 exactly how to run Ragas faithfulness and answer relevancy, and DeepEval faithfulness, with `claude-opus-5-5` as the judge model. The note covers the adapter each library needs, any extra package, how to pin library and model versions, how each library reports token usage for cost, and whether DeepEval's faithfulness adds anything beside Ragas's.

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] Note saved in docs/research/, citing primary sources: library docs, source code or release notes, with versions
- [x] States the exact Ragas and DeepEval versions to pin and the Claude adapter for each
- [x] Lists any package beyond anthropic, ragas and deepeval. If there is one, the user approves it before ticket 07
- [x] Recommends keeping or dropping DeepEval, with the reason
- [x] Says whether either library makes network calls at import, which matters for offline pytest

## Answer

Note: docs/research/ragas-deepeval-claude-judge.md. Both Claude 5.5 models reject non-default sampling settings, so "temperature 0" became pinned effort plus a response cache. Ragas's documented Anthropic route fails on Opus 5.5, so ticket 07 writes a project judge class. No extra package is needed. The user decided on 2026-10-05 to keep ragas==0.4.3 and drop DeepEval, and CLAUDE.md, the spec, the RUNBOOK and tickets 05, 07 and 08 now say so.
