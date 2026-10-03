---
name: step-review
description: "FilingIntel review of one RUNBOOK step or sub-step, along two axes: Standards (does the code follow this repo's coding standards?) and Spec (does it do what the RUNBOOK step, the approved step plan and OPEN-DECISIONS ask?). Runs both reviews in parallel sub-agents and reports them side by side. Use for workflow step 5 in CLAUDE.md, or when the user asks to review a step's changes."
---

Adapted from Matt Pocock's `code-review` skill (mattpocock/skills, skills/engineering/code-review). This copy reads its spec and standards from fixed files in this repo instead of an issue tracker.

Two-axis review of everything that changed since a fixed point:

- **Standards**: does the code follow this repo's coding standards?
- **Spec**: does the code do what the current step's spec files ask?

Both axes run as parallel sub-agents so neither sees the other's findings. This skill then puts the two reports side by side.

## Process

### 1. Pin the fixed point

The fixed point is the commit before the step or sub-step started. Take it from the user's arguments, or from the "Fixed point" line in the step's plan file (step 2). If neither gives one, ask.

Check that it resolves (`git rev-parse <fixed-point>`). The diff to review is `git diff <fixed-point>`, which covers committed and uncommitted changes. Also run `git log <fixed-point>..HEAD --oneline` and `git status --short` so new untracked files are included. If the diff is empty and there are no untracked files, stop and say so.

### 2. Collect the spec files

The spec is these files, and only these:

1. `.claude/plans/step-<N>.md`: the approved plan for this step, including the user's answers to its decisions. Where it conflicts with the RUNBOOK, the plan wins, because it records later decisions.
2. The current step's section of `docs/RUNBOOK.md`, including any amended acceptance-check section.
3. `docs/OPEN-DECISIONS.md`: the items listed for this step.
4. `CLAUDE.md`: the Invariants and Decisions sections.

If the plan file is missing, stop and ask the user. Don't review against the RUNBOOK alone, because the user's decisions would be missing.

Instructions the user gave only in chat are not in these files. If any of them matter for this review, write them into the plan file first, then run the review.

### 3. Collect the standards files

- `CLAUDE.md` (Workflow, Commands and Decisions sections)
- `~/.claude/rules/coding-style.md`, `~/.claude/rules/testing.md`, `~/.claude/rules/security.md`
- `pyproject.toml` (`[tool.ruff]`): anything ruff or pyright already enforces is skipped

The Standards axis also carries this smell baseline, a fixed set of Fowler code smells (_Refactoring_, ch. 3). Two rules apply to it:

- **The repo wins.** A documented repo standard always takes precedence. Where the repo allows something the baseline would flag, don't report the smell.
- **Always a judgement call.** Report each smell as a labelled heuristic ("possible Feature Envy"), never as a hard violation.

Each smell below gives what it is, then the fix:

- **Mysterious Name**: a function, variable or type whose name doesn't say what it does or holds. Fix: rename it. If no honest name comes to mind, the design is unclear.
- **Duplicated Code**: the same logic appears in more than one hunk or file in the change. Fix: extract the shared code and call it from both places.
- **Feature Envy**: a method that uses another object's data more than its own. Fix: move the method onto the object whose data it uses.
- **Data Clumps**: the same few fields or parameters keep travelling together. Fix: bundle them into one type and pass that.
- **Primitive Obsession**: a primitive or string standing in for a domain concept. Fix: give the concept its own small type.
- **Repeated Switches**: the same `if` cascade or `match` on the same type appears in several places in the change. Fix: one lookup table both sites share, or polymorphism.
- **Shotgun Surgery**: one logical change forces scattered edits across many files. Fix: gather what changes together into one module.
- **Divergent Change**: one module is edited for several unrelated reasons. Fix: split it so each module changes for one reason.
- **Speculative Generality**: abstractions, parameters or hooks for needs the spec doesn't have. Fix: delete them and inline the code until a real need appears.
- **Message Chains**: long `a.b().c().d()` navigation the caller shouldn't depend on. Fix: one method on the first object that does the walk.
- **Middle Man**: a class or function that mostly passes calls on. Fix: remove it and call the real target directly.
- **Refused Bequest**: a subclass that ignores or overrides most of what it inherits. Fix: drop the inheritance and use composition.

### 4. Spawn both sub-agents in parallel

**Standards sub-agent prompt** must include:

- The diff command, the commit list and the untracked files.
- The standards file paths from step 3, plus the smell baseline pasted in full, because the sub-agent can't see it otherwise.
- The brief: "For each file or hunk, report (a) every place the diff breaks a documented standard, citing the file and the rule, and (b) any baseline smell you find, naming it and quoting the hunk. Mark documented-standard breaches as hard or judgement calls; baseline smells are always judgement calls. Skip anything ruff or pyright enforces. Under 400 words."

**Spec sub-agent prompt** must include:

- The diff command, the commit list and the untracked files.
- The spec file paths from step 2 and the step number.
- The brief: "Report (a) requirements or acceptance checks the spec asks for that are missing or partial; (b) behaviour in the diff that the spec didn't ask for (scope creep); (c) requirements that look implemented but where the implementation looks wrong; (d) any change that weakens an acceptance check, test threshold or CLAUDE.md invariant, such as a loosened assertion, a skipped or xfail test, or a lowered threshold. Quote the spec line for each finding. Under 400 words."

### 5. Aggregate

Present the two reports under `## Standards` and `## Spec` headings, verbatim or lightly cleaned. Don't merge or rerank the findings across the two axes.

End with one line: the number of findings on each axis, and the worst issue within each axis, if there is one. Don't pick a single worst issue across both axes.

Per CLAUDE.md, every Spec finding is either fixed or reported to the user with the reason it can't be fixed. Never resolve a finding by weakening an acceptance check.

## Why two axes

A change can pass one axis and fail the other:

- Code that follows every standard but builds the wrong thing passes Standards and fails Spec.
- Code that does exactly what the step asked but breaks the repo's conventions passes Spec and fails Standards.

Reporting them separately stops one axis from hiding the other.
