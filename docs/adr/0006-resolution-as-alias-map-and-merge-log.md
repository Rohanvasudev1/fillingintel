# Entity resolution writes an alias map and an append-only merge log, never new keys

Entity resolution leaves every extracted node key as Step 8 built it. It writes a merge log of merge and split events, each with its deciding evidence and method (deterministic, LLM or user), and an alias map from each extracted key to a canonical key, rebuilt by replaying the log. Undoing a merge means appending a split, which works only because the original keys still exist. Step 9 writes nothing to Neo4j; Step 10 loads nodes through the alias map.

Decided on 2026-10-10 in the Step 9 grilling.

## Considered options
- **Rewrite keys in the extract files.** Simpler to load, but a wrong merge could not be undone without extracting again, and the merge's evidence would be lost.
- **Merge nodes in Neo4j.** Couples resolution to the write path before Step 10 exists, and a reversal means unpicking merged evidence lists.
