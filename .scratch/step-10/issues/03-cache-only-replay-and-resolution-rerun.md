# 03: Cache-only replay and resolution rerun

**What to build:** All 24 filings are extracted again from the response cache under the new metric-value key, at $0, and resolution is rerun so the alias map matches the new keys, with proof that nothing else in the map moved. Spec: `.scratch/step-10/spec.md` (Replay).

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] `extract.run` gains a cache-only flag: before any call, it checks every chunk is in the response cache and refuses, naming the missing chunks, if not. Tested with a cache missing one chunk.
- [ ] The replay writes new run numbers for each filing; old run files and reports stay. NVDA FY2026 replays its `extract/v2` run only.
- [ ] Intel chunk 0000050863-24-000076:0017 stays a failed chunk in the replay, as before (it has no cached reply).
- [ ] `resolve.run` reruns with cached adjudicator answers only and makes no API call.
- [ ] A check compares the new alias map with the committed one for every label except MetricValue and stops on any difference, listing it. If it stops, the ticket stops for the user.
- [ ] The PR reports conflicts before and after (corpus total and metric-value share) and the number of metric values before and after.
- [ ] New run reports, merge log, alias map and resolution run report committed. Commands run with `env -u ANTHROPIC_BASE_URL`.
- [ ] `mattpocock-skills:code-review`; ruff and pytest green.
