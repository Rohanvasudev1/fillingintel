# 04: Question filter

**What to build:** the shared question filter parser from ADR-0001, applied by the vector arm as a hard filter. Each results file reports the parser's exact match against the eval labels and its filter-excluded-gold count.

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] Pure function of the question text plus the corpus's filing list, which the caller passes in
- [ ] Tests for every ADR-0001 rule: fiscal years, fiscal quarters, AMD and Intel calendar quarters, NVIDIA calendar quarters setting nothing, bare years such as "August 2025" setting nothing, form types, "latest" with and without a form, unions, no filter when unsure
- [ ] A test over the dev records asserts filter-excluded-gold is 0
- [ ] The vector arm applies the filter in SQL with bound parameters, never string formatting
- [ ] Per-question records show the filter used and, for multi-company questions, which named companies got no retrieved chunks
- [ ] Reviewed with `mattpocock-skills:code-review`, every finding fixed or explained
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass
