# The quality gate scores retrieval only, offline, from a committed snapshot

The quality gate on each pull request runs the control arm's real search over a committed snapshot of the database and scores retrieval only. It makes no API calls and does not score answers. A gate has to give the same result every time it runs on the same code. Retrieval meets that, because the vectors are stored, search is exact (ADR-0002) and ties break by chunk ID. The judged scores do not: an uncached rerun of the baseline moved them by up to 0.031. Running offline also keeps CI's no-network rule and keeps API keys out of GitHub.

The gate runs all 82 answerable `dev` questions rather than the RUNBOOK's "about 40", because offline retrieval is fast and free. It compares recall@5, recall@10 and the wrong-evidence rate, per class and pooled, against `benchmarks/gate_baseline.json`, and any drop fails. The snapshot is one gzipped file holding the real `filings`, `chunks` and `chunk_embeddings` rows plus the gated questions' query vectors, built from the local database and loaded into the real schema.

Decided on 2026-10-06 in the Step 6 grilling.

## Considered options
- **Live eval in CI with API keys as secrets.** Catches answer regressions, but costs about $3 per push, scores vary between runs, and it breaks the no-network rule.
- **Replaying the committed response cache.** Any retrieval change produces new prompts, which miss the cache, so it fails exactly when a gate is needed.
- **A tolerance on each number.** With no run-to-run noise, a tolerance only hides real drops.

## Consequences
- A regression only in answers, such as a worse prompt, passes the gate. Answer and judge scores are checked by hand with `eval.run` before merging such a PR. A manually triggered live eval can be added later.
- A deliberate trade-off updates the gate baseline in the same PR. An agent never lowers it to get a PR through (invariant 5); a lowering needs the user's yes and a BUILD-LOG line.
- Rebuilding chunks or embeddings means rebuilding the snapshot, which adds 10–12 MB to git history each time.
