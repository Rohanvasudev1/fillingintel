# 07: Tracing for retrieval and answers

**What to build:** when `PHOENIX_COLLECTOR_ENDPOINT` is set, `python -m eval.run` sends one trace per question to Phoenix: a CHAIN root span with the question text, a CHAIN span for the question filter, an EMBEDDING span for the query embedding, a RETRIEVER span listing retrieved chunk IDs and scores, and an LLM span for answer generation. When it is unset, no tracing objects are built and nothing is sent. Adds the three approved dependencies: `opentelemetry-sdk==1.45.0`, `opentelemetry-exporter-otlp-proto-http==1.45.0`, `openinference-semantic-conventions==0.1.41`. See the spec's Tracing section and docs/research/phoenix-tracing.md sections 2–4.

**Blocked by:** 01, 06

**Status:** ready-for-agent

**Notes from ticket 06:**
- `.env.example` names the text-capture setting `FILINGINTEL_TRACE_TEXT` (off unless `true`). Use that name, or rename it in `.env.example` in the same change.
- `.env.example` gives `PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006`, the base URL. Phoenix takes spans at `POST /v1/traces`. An `OTLPSpanExporter(endpoint=...)` argument is used as given, with no path added, so the tracing module appends `/v1/traces`.

- [ ] A tracing module builds a `TracerProvider` with a `BatchSpanProcessor` and an OTLP HTTP exporter with a 2-second timeout only when the endpoint is set. It never calls `trace.set_tracer_provider`. `eval.run` shuts the provider down before exiting.
- [ ] The vector arm takes an optional tracer; with none, it records no spans.
- [ ] Span names, kinds and attribute names follow OpenInference, using the conventions package constants.
- [ ] LLM spans carry `llm.model_name`, the provider and token counts. A response-cache replay carries no `llm.token_count.*`. Claude's prompt count is uncached input plus cache reads plus cache writes.
- [ ] Our own per-call cost and the Voyage cost go in project-namespaced attributes.
- [ ] Prompt and answer text appear on spans only when the text-capture setting is on. No attribute ever holds an API key or request header (tested).
- [ ] An autouse pytest fixture removes `PHOENIX_COLLECTOR_ENDPOINT` and `OTEL_EXPORTER_OTLP_*` for every test; a test shows the tracing module builds nothing when the variable is unset.
- [ ] Tracing tests (test-first) pass their own `TracerProvider` with an in-memory exporter and check names, kinds, parent links and attributes for one question run with the fake embedder and fake answer model.
- [ ] Tracing changes no score and no run file field: a test compares an untraced and a traced run's output with fakes.
- [ ] Demo: with Phoenix up, a cached traced `eval.run` over a few dev questions shows their trees in the Phoenix UI. Screenshot or description in the BUILD-LOG entry. The run makes no paid calls (all answers replayed from the cache).
- [ ] With Phoenix stopped and the endpoint set, a run still finishes, with at most a few seconds of export delay at shutdown.
- [ ] `uv.lock` adds only the expected packages and changes no existing version.
- [ ] `uv run ruff check .` and `uv run --env-file .env pytest` pass.
