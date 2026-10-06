# 04: Quality gate in CI

**What to build:** the CI workflow runs the quality gate on every pull request and every push to `main`. A new `quality-gate` job uses the same pinned pgvector service as `lint-and-test`, loads the committed snapshot into it, and runs `python -m eval.gate`. Triggers change from every push to `pull_request` plus `push` to `main`, with no `paths` filters, so CI runs once per PR push and a required check can never wait on a skipped workflow. This ticket is the project's first branch and PR.

**Blocked by:** 03

**Status:** done (2026-10-06; PR #3 merged, push run 37455002708 on `main` green)

- [x] `ci.yml` has a `quality-gate` job, separate from `lint-and-test`, with no network access beyond the service container and no API keys.
- [x] Triggers are `pull_request` and `push` to `main` only; no `paths` or `paths-ignore` filters.
- [x] The gate's comparison table appears in the job summary on the PR.
- [x] The work is on its own branch, opened as a PR with the ticket's summary and test plan, and both `lint-and-test` and `quality-gate` are green on it.
- [x] After review and verification, the PR is merged and the `push` run on `main` is green.
- [x] Asking the user before changing CI is satisfied by the approved spec; any change beyond the spec is raised first.
