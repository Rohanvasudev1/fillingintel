# 02: Unit class in the metric-value key, values compared across scales

**What to build:** A percentage and an amount with the same concept name become two metric values, and the same figure at two scales stops counting as a conflict. Spec: `.scratch/step-10/spec.md` (Metric-value key). Vocabulary: CONTEXT.md (metric value, unit class).

**Blocked by:** None (can start immediately)

**Status:** done

- [x] A hand-written unit table maps each of the 22 unit strings in the corpus to a unit class (currency, percent, per share, shares, count, other) and a scale. An unknown unit string fails with its name.
- [x] The metric-value key is `{accession_no}:{subject key}:{norm(concept)}:{unit class}:{period}`, escaped as today. Tested with NVIDIA FY2026 net income in percent and in USD giving two keys.
- [x] Inside one key, values are compared after conversion to one scale. Two values that differ by no more than half a unit in the last printed digit of the less precise one agree: the more precise entry wins and no conflict is counted. Tested with AMD FY2024-Q1 net revenue (0000002488-24-000056; the ticket first said Q1 FY2025), $5.5 billion against $5,473 million.
- [x] Values that still disagree follow the Step 8 rule (most confident, ties to the later chunk) and are counted as conflicts, as today.
- [x] The stored value and unit are the winning entry's as printed.
- [x] The ontology version, rendered extract prompt and its hash are unchanged (a test asserts the hash).
- [x] Test-first; `mattpocock-skills:code-review`; ruff and pytest green.
