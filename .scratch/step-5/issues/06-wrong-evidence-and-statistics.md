# 06: Wrong evidence and statistics

**What to build:** each results file reports the wrong-evidence rate, and every cell carries a bootstrap 95% interval.

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] Wrong evidence counts a question when a hard negative ranks above a gold chunk or is cited, overall and per hard-negative label
- [ ] Bootstrap 95% intervals on every cell, reproducible under the recorded seed
- [ ] Classes with fewer than 10 records in the scored split get the label "directional"
- [ ] Tests against hand-computed values on real eval records
- [ ] Reviewed with `mattpocock-skills:code-review`, every finding fixed or explained
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass
