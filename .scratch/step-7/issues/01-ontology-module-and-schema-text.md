# 01: Ontology module and schema text

**What to build:** the ontology as code, with no database. A new `graph` package holds every node label and edge type as frozen data: kind (structural or extracted), key properties, required properties with types, allowed start and end labels for each edge type, and a hand-written one-line description for each. From that data the package generates the schema text for prompts, in a fixed order, and exposes `ONTOLOGY_VERSION = 1` and the SHA-256 of the text. Labels, edge types, endpoints and required properties are exactly those in the spec and in CLAUDE.md Decisions (Step 7). See ADR-0004 for the evidence properties.

**Blocked by:** None (can start immediately)

**Status:** done (2026-10-07, PR #12)

- [x] The ontology lists the 4 structural labels (Company, Filing, Period, Chunk) and the 10 extracted labels, each with kind, keys, required properties and types, and a description.
- [x] The ontology lists the 20 edge types, each with kind, allowed start and end labels, required properties (`role` on `INVOLVED_IN` and `HOLDS_ROLE_AT`; optional `stake` on `OWNS`) and a description. `COMPETES_WITH` is marked symmetric.
- [x] Extracted edges require `chunk_ids` and `evidence_spans`; `EVIDENCED_BY` requires `evidence_span`; every node and edge requires `ontology_version`. Chunk has no text property.
- [x] The definitions can't be changed at runtime (a test tries and fails).
- [x] A test checks every edge type's endpoint list against the table in CLAUDE.md Decisions (Step 7).
- [x] The schema text lists labels then edge types in a fixed order, with properties, endpoints and descriptions, and is the same on every call.
- [x] A test pins the schema text's SHA-256 for version 1, so any ontology edit fails until the version and the pin are updated together.
- [x] Rule tests are written first and shown failing before the code (`/tdd`).
- [x] No new dependency. `uv run ruff check .` and `uv run --env-file .env pytest` pass.
