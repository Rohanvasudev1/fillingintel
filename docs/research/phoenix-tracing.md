# Phoenix tracing and the required CI check (Step 6)

Research note for Step 6, written 2026-10-06, before any tracing code exists. The decisions already taken: Phoenix runs as a pinned service in `docker-compose.yml`; the code uses `opentelemetry-sdk` with an OTLP exporter and hand-written spans named after OpenInference conventions; tracing is off unless `PHOENIX_COLLECTOR_ENDPOINT` is set; CI and pytest stay offline; and the CI job becomes a required status check on a protected `main`. This note checks those decisions against primary sources and fills in the details. Sources: Phoenix docs at arize.com/docs/phoenix (fetched 2026-10-06); the Phoenix repository at tag `arize-phoenix-v20.19.0` (Dockerfile, LICENSE, release workflow) read with `gh api`; Docker Hub tag listings from `hub.docker.com/v2/repositories/arizephoenix/phoenix/tags`; PyPI JSON metadata; the published wheels of `opentelemetry-sdk` and `opentelemetry-api` 1.45.0, `opentelemetry-exporter-otlp-proto-http` 1.45.0, `opentelemetry-exporter-otlp-common` and `opentelemetry-exporter-http-transport` 0.66b0, `openinference-semantic-conventions` 0.1.41, `openinference-instrumentation` 0.1.70, `arize-phoenix-otel` 0.17.2 and the `arize-phoenix` 20.19.0 server, unpacked in a scratch directory; the OpenInference spec (github.com/Arize-ai/openinference, `spec/semantic_conventions.md`); the OpenTelemetry SDK environment variable spec; and GitHub Docs, read from the source Markdown in github.com/github/docs. Behaviour claims marked "(ran)" were checked by running code in a throwaway `uv run --no-project --python 3.11` environment, never in the project. Lock-file effects were checked by running `uv add --no-sync` on a scratch copy of `pyproject.toml` and `uv.lock`. Anything not confirmed from a primary source is marked "unverified".

## Summary and recommendation

- Image: `arizephoenix/phoenix:20.19.0`, released 2026-10-01, multi-arch (amd64, arm64), digest `sha256:d240d8d4e364ee3483ed2de41ec12b760cc90bb0d4c6e2f190388772aba6fe1f`. Port 6006 serves the UI and OTLP over HTTP at `/v1/traces`; port 4317 serves OTLP over gRPC. Pin `20.19.0`, not `latest` or `20` (section 1).
- Persistence: with no database URL, Phoenix writes SQLite to `$PHOENIX_WORKING_DIR/phoenix.db`, default `~/.phoenix/phoenix.db` inside the container, which is lost when the container is removed. Set `PHOENIX_WORKING_DIR=/mnt/data` and mount a named volume there, as the Phoenix Docker docs show. Postgres via `PHOENIX_SQL_DATABASE_URL` is possible but not needed (section 1.4).
- Licence: the Phoenix server is Elastic License 2.0. Running it locally for this project is allowed; offering it to third parties as a hosted service is not. `arize-phoenix-otel` and `openinference-semantic-conventions` are Apache-2.0 (section 1.6).
- Telemetry: the Phoenix UI page loads a Scarf.sh tracking pixel by default and FullStory when an org ID is set. Set `PHOENIX_TELEMETRY_ENABLED=false` in the compose service. `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false` also stops web fonts and a WASM binary download (section 1.5).
- Auth is off by default. Bind both ports to `127.0.0.1` in compose so the UI and collector are not reachable from the network (section 1.3).
- Python packages: `opentelemetry-sdk==1.45.0` and `opentelemetry-exporter-otlp-proto-http==1.45.0`. On the current lock this adds 10 packages (8 OpenTelemetry packages, `protobuf` 7.36.2, `googleapis-common-protos` 1.75.5) and changes no existing version. Use HTTP, not gRPC: the gRPC exporter needs `grpcio` (7.2 MB Linux wheel, 12.4 MB macOS wheel). Both need approval under "ask before adding a dependency" (section 2).
- Do not use `arize-phoenix-otel`. It adds `grpcio`, `wrapt`, the gRPC exporter and `openinference-instrumentation` (14 new packages). Its `register()` with no endpoint and no env var exports to `localhost:4317` rather than turning off, defaults to `SimpleSpanProcessor`, sets the global provider, prints to stdout, and walks up from the working directory reading any `.env.phoenix` file. None of that fits "off unless `PHOENIX_COLLECTOR_ENDPOINT` is set" (section 2.4).
- Use `BatchSpanProcessor` with a short exporter timeout (2 s) and call `provider.shutdown()` in a `finally` at the end of each CLI. With Phoenix down, `SimpleSpanProcessor` blocked about 7 s per span (21.5 s for 3 spans); `BatchSpanProcessor` cost 6.9 s once at shutdown with the default timeout and 1.1 s with a 2 s timeout (ran, section 2.3).
- Span kinds: CHAIN for the per-question root span and the question filter, EMBEDDING for the Voyage call, RETRIEVER for pgvector search, LLM for the answer call and for each judge API call, with an EVALUATOR parent per judged metric (section 3.1).
- Cost: Phoenix ignores `llm.cost.*` on ingest. It computes cost itself only for spans with kind `LLM` and a `llm.model_name`, from `llm.token_count.*` and its built-in price table. That table maps `claude-sonnet-5-5` to its `claude-sonnet-5` entry ($2 / $10 per MTok) and has `gpt-6-luna` ($0.10 / $0.50), both equal to `retrieve/pricing.py`. It has no Voyage entry. On a response-cache hit, leave out `llm.token_count.*`, or Phoenix will bill the replayed answer again (section 3.4).
- Depend on `openinference-semantic-conventions==0.1.41` for attribute names: a 12.7 KB wheel, Apache-2.0, no dependencies. A typo in a hand-written name fails silently in the Phoenix UI. Hard-coding about 25 strings in one module is the fallback if the user prefers no new package (section 3.5).
- Tests: build a `TracerProvider` per test with `SimpleSpanProcessor(InMemorySpanExporter())` and pass it into the code, never through `trace.set_tracer_provider`, which works once per process and only logs a warning on later calls (ran). Add an autouse fixture that removes `PHOENIX_COLLECTOR_ENDPOINT` and `OTEL_EXPORTER_OTLP_*`: pytest-socket allows localhost, so it would not stop a test from exporting to a local Phoenix (section 4).
- Off mode: when the variable is unset, do not build an SDK provider. `trace.get_tracer()` then returns a `ProxyTracer` whose spans are `NonRecordingSpan`s; nothing is exported and nothing touches the network (ran). Importing the SDK and the HTTP exporter made no socket calls (ran) (section 4.3).
- Thread pools: `eval/judging/runner.py` judges in a `ThreadPoolExecutor`. Spans started in a worker thread lose their parent and become separate traces unless the task runs under `contextvars.copy_context().run` (ran, section 4.4).
- GitHub: the required check name is the job's `name:` if set, else the job id. Today that is `lint-and-test` (read from the check runs on c7da22a). A workflow skipped by `paths` or `branches` filters leaves a required check "Pending" and blocks the merge; a job skipped by `if:` reports success. Keep `ci.yml` free of path filters (section 5).
- A required check blocks today's workflow of pushing tickets straight to `main`: a protected branch accepts a direct push only when the required checks have already passed on that commit. Step 6 should move to a branch and PR per ticket or step, or give the owner a bypass (section 5.4).
- For this personal public repository, use a ruleset rather than classic branch protection, with `~DEFAULT_BRANCH` as target, the `required_status_checks` rule pinned to the GitHub Actions app (integration ID 15368), and the repository admin role on the bypass list in `pull_request` mode. Change `on: push` to `on: push: branches: [main]` so PR branches do not run the workflow twice (sections 5.3 to 5.6).

## 1. The Phoenix server image

### 1.1 Image and tag

- Image name: `arizephoenix/phoenix` on Docker Hub. The Docker deployment page gives `docker run -p 6006:6006 -p 4317:4317 -i -t arizephoenix/phoenix:latest` and says "You should pin the phoenix version for production". Source: https://arize.com/docs/phoenix/self-hosting/deployment-options/docker.
- Latest stable release: `arize-phoenix-v20.19.0`, published 2026-10-01T22:59:10Z, not a prerelease (`gh api repos/Arize-ai/phoenix/releases`). The two before it were 20.18.0 and 20.17.0, both on 2026-09-30, so releases are frequent.
- Docker Hub tags (https://hub.docker.com/v2/repositories/arizephoenix/phoenix/tags, read 2026-10-06): `20.19.0`, `20.19`, `20`, `version-20.19.0` and `latest` all point to `sha256:d240d8d4e364ee3483ed2de41ec12b760cc90bb0d4c6e2f190388772aba6fe1f`, pushed 2026-10-01, for amd64 and arm64. Variants `20.19.0-nonroot` (`sha256:f2109e09…`) and `20.19.0-debug` exist. `nightly` and `commit-<sha>` tags are pre-release builds.
- The release workflow publishes three variants. The unsuffixed tags use `gcr.io/distroless/python3-debian13`, `-nonroot` uses its `:nonroot` image, and `-debug` its `:debug` image. Sources: `.github/workflows/docker-build-release.yml` (lines 57, 77, 101 to 117) and `.github/actions/docker-build-image/action.yml` (lines 117 to 119) at tag `arize-phoenix-v20.19.0`.
- Recommendation: `image: arizephoenix/phoenix:20.19.0`, matching the exact-tag style of `pgvector/pgvector:0.8.6-pg16` and `neo4j:5.26.31-community`. A digest pin (`@sha256:d240…`) is stricter but not what the other services do.

### 1.2 Ports and routes

- 6006 is the "UI and OTLP HTTP collector", 4317 the "OTLP gRPC collector", 9090 optional Prometheus metrics (Docker deployment page). The Dockerfile has `EXPOSE 6006`, `EXPOSE 4317` and `EXPOSE 9090` (lines 164 to 168) and runs `python -m phoenix.server.main serve` (line 172).
- The HTTP collector is `POST /v1/traces` (router prefix `/v1` in `phoenix/server/api/routers/v1/__init__.py` line 54, handler in `routers/v1/traces.py`). It accepts only `Content-Type: application/x-protobuf` and `Content-Encoding` gzip or deflate (`traces.py` lines 477 to 489). The OTLP HTTP exporter sends protobuf, so it fits; OTLP/JSON does not.
- Health routes: `GET /healthz` and `GET /readyz` (`phoenix/server/app.py` lines 776 and 781).
- The images are distroless, so they have no shell or `curl`. A compose `healthcheck` would have to call Python, for example `["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:6006/healthz')"]`. Unverified: not run against the image.

### 1.3 Environment variables that matter

From https://arize.com/docs/phoenix/self-hosting/configuration and `phoenix/config.py` in the 20.19.0 wheel:

| Variable | Default | Use here |
|---|---|---|
| `PHOENIX_PORT` | 6006 | leave |
| `PHOENIX_GRPC_PORT` | 4317 | leave |
| `PHOENIX_HOST` | `0.0.0.0` | leave; restrict with the compose port binding |
| `PHOENIX_WORKING_DIR` | `~/.phoenix` (`config.py` lines 1146 to 1154) | set to `/mnt/data` and mount a volume |
| `PHOENIX_SQL_DATABASE_URL` | SQLite at `{working_dir}/phoenix.db` (`config.py` lines 3373 to 3381) | leave unset |
| `PHOENIX_ENABLE_AUTH` | false (`config.py` lines 1237 to 1241) | leave off; bind to 127.0.0.1 |
| `PHOENIX_TELEMETRY_ENABLED` | true | set `false` |
| `PHOENIX_ALLOW_EXTERNAL_RESOURCES` | true (`config.py` lines 3848 to 3853) | set `false` (section 1.5) |
| `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS` | 0, "infinite retention" | leave |
| `PHOENIX_ENABLE_PROMETHEUS` | false | leave |

Auth is off by default, so anyone who can reach port 6006 can read traces and post spans. Publish the ports as `"127.0.0.1:6006:6006"` and `"127.0.0.1:4317:4317"`. The existing Postgres and Neo4j services publish on all interfaces; that is outside this step and is only flagged.

### 1.4 Persistence

- The configuration page says that without a URL "by default Phoenix starts with a file-based SQLite database in a temporary folder". The 20.19.0 source disagrees: `get_env_database_connection_str()` returns `sqlite:///{working_dir}/phoenix.db`, and the working directory defaults to `Path.home() / ".phoenix"` (`config.py` lines 1146 to 1154 and 3373 to 3381). Trust the source. Either way the file lives in the container's writable layer, so `docker compose down` followed by `up` loses all traces. A plain restart keeps them, since the container is not recreated (Docker behaviour, not checked against a Phoenix doc).
- The Docker deployment page shows the SQLite setup: `PHOENIX_WORKING_DIR=/mnt/data` with a volume `phoenix_data:/mnt/data`. This matches `postgres_data` and `neo4j_data` in `docker-compose.yml`.
- Postgres option: `PHOENIX_SQL_DATABASE_URL=postgresql://…`, with `PHOENIX_SQL_DATABASE_SCHEMA` to keep Phoenix tables in their own schema (configuration page). Pointing Phoenix at the project's pgvector database would mix trace tables with the corpus and add a startup dependency. SQLite on a volume is enough for one developer.
- With the nonroot variant the volume must be writable by the nonroot user. Unverified; the root variant avoids the question.

Suggested service (for review, not applied):

```yaml
  phoenix:
    image: arizephoenix/phoenix:20.19.0
    environment:
      PHOENIX_WORKING_DIR: /mnt/data
      PHOENIX_TELEMETRY_ENABLED: "false"
      PHOENIX_ALLOW_EXTERNAL_RESOURCES: "false"
    ports:
      - "127.0.0.1:6006:6006"
      - "127.0.0.1:4317:4317"
    volumes:
      - phoenix_data:/mnt/data
```

The 4317 mapping can be dropped if only HTTP export is used (section 2.2).

### 1.5 Telemetry it sends

- `PHOENIX_TELEMETRY_ENABLED` is the "Master toggle for telemetry pixels (FullStory and Scarf.sh)", default true (`config.py` lines 150 to 155).
- Scarf.sh: with telemetry on, `get_env_scarf_sh_pixel_id()` returns `PHOENIX_SCARF_SH_PIXEL_ID` or the built-in ID `98877b05-7d80-493e-ab95-97c104785d1e` (`config.py` lines 3713 to 3723). The UI template then loads `https://static.scarf.sh/a.png?x-pxid=…` (`phoenix/server/templates/index.html` line 156). So by default every browser load of the UI pings Scarf.
- FullStory session recording loads only when `PHOENIX_FULLSTORY_ORG` is set (`config.py` lines 3700 to 3710; `index.html` lines 102 to 109). It is unset by default.
- The configuration page says: "Only basic web analytics are collected (e.g., page views, UI interactions). No trace data or sensitive information is ever collected." This is Arize's claim; the source supports it only to the extent that the pixels are browser-side.
- `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false` means "Phoenix makes no public-internet requests on its own initiative, such as web fonts, telemetry, or the WASM sandbox binary download", and it forces telemetry off (`config.py` lines 157 to 160 and 3683 to 3697). Setting both is belt and braces.

### 1.6 Licence

- The repository `LICENSE` at tag `arize-phoenix-v20.19.0` is "Elastic License 2.0 (ELv2)". Its limitations include: "You may not provide the software to third parties as a hosted or managed service". PyPI lists `arize-phoenix` 20.19.0 as `Elastic-2.0`.
- For this project Phoenix runs locally as a developer tool and is not redistributed or offered to others, so ELv2 does not restrict it. If FilingIntel were ever deployed with a public Phoenix UI for other users, re-read the licence first.
- The client-side packages are Apache-2.0 per PyPI: `opentelemetry-*`, `openinference-semantic-conventions`, `openinference-instrumentation` and `arize-phoenix-otel`. The project code only depends on those, never on the ELv2 server package.

## 2. Smallest OpenTelemetry setup

### 2.1 Packages and versions

From PyPI JSON (`https://pypi.org/pypi/<pkg>/json`, read 2026-10-06). All were uploaded 2026-09-25 unless noted, and all declare `requires_python >=3.10`.

| Package | Latest | Notes |
|---|---|---|
| `opentelemetry-api` | 1.45.0 | depends on `typing-extensions` |
| `opentelemetry-sdk` | 1.45.0 | pins `opentelemetry-api==1.45.0`, `opentelemetry-semantic-conventions==0.66b0` |
| `opentelemetry-exporter-otlp-proto-http` | 1.45.0 | needs `opentelemetry-exporter-http-transport[urllib3]==0.66b0`, `opentelemetry-exporter-otlp-common==0.66b0`, `-otlp-proto-common==1.45.0`, `opentelemetry-proto==1.45.0`, `googleapis-common-protos~=1.52` |
| `opentelemetry-exporter-otlp-proto-grpc` | 1.45.0 | as above plus `grpcio>=1.63.2,<2` on 3.11 |
| `opentelemetry-proto` | 1.45.0 | `protobuf>=5.0,<8.0` |
| `protobuf` | 7.36.2 (2026-09-17) | about 0.34 MB Linux wheel |
| `grpcio` | 1.84.0 (2026-09-14) | 7.2 MB cp311 manylinux x86_64 wheel, 12.4 MB macOS universal2 |

New in the 1.45 line: the HTTP exporter no longer depends on `requests`. It sends through `opentelemetry-exporter-http-transport` with its `urllib3` extra (`requires_dist` above; `import requests` appears only under `TYPE_CHECKING`, `trace_exporter/__init__.py` lines 52 to 53). `requests` remains an optional extra.

Lock effect (scratch copy, `uv add --no-sync opentelemetry-sdk==1.45.0 opentelemetry-exporter-otlp-proto-http==1.45.0`): 10 packages added, namely `googleapis-common-protos` 1.75.5, `opentelemetry-api`, `-exporter-http-transport`, `-exporter-otlp-common`, `-exporter-otlp-proto-common`, `-exporter-otlp-proto-http`, `-proto`, `-sdk`, `-semantic-conventions`, and `protobuf` 7.36.2. No existing package changes version. `urllib3` 2.8.0 and `typing-extensions` 4.16.0 are already in `uv.lock`.

### 2.2 HTTP or gRPC

- Phoenix accepts both: gRPC on 4317, HTTP on 6006 at `/v1/traces` (section 1.2). Phoenix's own docs call gRPC "more performant" and HTTP "simpler" (setup-using-phoenix-otel page, via search result; the full page was not fetched).
- The gRPC exporter pulls in `grpcio`, a compiled wheel of 7 to 12 MB. The HTTP exporter uses `urllib3`, already in the lock.
- Volume here is small: one trace per question, about 10 to 15 spans, a few hundred questions per run. The performance difference does not matter.
- Recommendation: HTTP, endpoint `{PHOENIX_COLLECTOR_ENDPOINT}/v1/traces`, for example `http://localhost:6006/v1/traces`.

The exporter's endpoint argument must be the full URL. Its docstring says the argument is the full signal path (`http://collector:4318/v1/traces`); only the `OTEL_EXPORTER_OTLP_ENDPOINT` env var gets `/v1/traces` appended (`trace_exporter/__init__.py` lines 119 to 120 and 139; `_common/__init__.py` lines 129 to 138). The project should append `/v1/traces` itself.

### 2.3 Batch or simple processor for a CLI

- `BatchSpanProcessor` exports from a daemon thread on a schedule (default delay 5,000 ms, export timeout 30,000 ms: `sdk/trace/export/__init__.py` lines 41 to 43; OTel spec defaults are the same, https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/). `SimpleSpanProcessor` exports synchronously when each span ends.
- The SDK `TracerProvider` registers `atexit.register(self.shutdown)` unless `shutdown_on_exit=False` (`sdk/trace/__init__.py` lines 1257 and 1283 to 1284). Shutdown flushes the batch processor, so a normal exit does not lose spans. An explicit `provider.shutdown()` in a `finally` is clearer and also covers `KeyboardInterrupt`; `os._exit` or a kill skips atexit.
- Measured with no collector listening (ran, `127.0.0.1:59999`): `SimpleSpanProcessor` took 21.5 s to end 3 spans, about 7 s each, because each export retries until the 10 s default timeout (`_DEFAULT_TIMEOUT = 10`, `_common/__init__.py` line 50). `BatchSpanProcessor` ended the spans at once and took 6.9 s in `shutdown()`, or 1.1 s with `OTLPSpanExporter(timeout=2)`. The exporter logs a warning and an error; it does not raise.
- Recommendation: `BatchSpanProcessor(OTLPSpanExporter(endpoint=…, timeout=2))`. When `PHOENIX_COLLECTOR_ENDPOINT` is set but Phoenix is not running, a run then loses about a second at exit, not seconds per span.

### 2.4 Does `arize-phoenix-otel` add value?

What it is: a wrapper, `phoenix.otel.register()`, around the same SDK, plus re-exports of OpenInference names (`phoenix/otel/__init__.py`). Version 0.17.2, uploaded 2026-09-28, Apache-2.0, `requires_python >=3.11,<3.15`.

Dependencies (PyPI `requires_dist`): `openinference-instrumentation>=0.1.38`, `openinference-semantic-conventions>=0.1.17`, `opentelemetry-exporter-otlp` (the meta-package with both gRPC and HTTP exporters), `opentelemetry-exporter-otlp-proto-http>=1.45.0`, `opentelemetry-proto`, `opentelemetry-sdk`, `opentelemetry-semantic-conventions`, `typing-extensions`, `wrapt`. On the scratch lock it adds 14 packages, including `grpcio` 1.84.0, `wrapt` 2.5.0 and both exporters.

Behaviour, from `phoenix/otel/otel.py` and `settings.py` in the 0.17.2 wheel:

- It imports the gRPC exporter at module level (`otel.py` lines 13 to 16), so `import phoenix.otel` loads `grpc` (ran: `grpc` present in `sys.modules`; import took 1.3 s against 0.5 s for the plain SDK and HTTP exporter).
- `register()` defaults: `batch=False` (simple processor), `set_global_tracer_provider=True`, `verbose=True` (prints a banner to stdout), `auto_instrument=False` (`otel.py` lines 65 to 74, 174 to 196).
- With no endpoint argument and no env var, it falls back to `http://localhost:6006` and, without `use_http`, builds a gRPC endpoint (`_normalized_endpoint`, `otel.py` lines 803 to 822). Run with the variable unset, it printed "Collector Endpoint: localhost:4317, Transport: gRPC" (ran). So it is never off by default.
- Config discovery: unless `PHOENIX_DISCOVER_CONFIG` is false, it searches the current directory and every parent for a `.env.phoenix` file owned by the current user and reads its `PHOENIX_`-prefixed keys, such as the endpoint, API key and headers (`settings.py` lines 26 to 28, 68 to 92, 124 to 136). It also reads `PHOENIX_COLLECTOR_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `PHOENIX_GRPC_PORT`, `PHOENIX_PROJECT`, `PHOENIX_PROJECT_NAME`, `PHOENIX_CLIENT_HEADERS` and `PHOENIX_API_KEY` (`settings.py` lines 11 to 23). That clashes with invariant 7's rule that config comes from environment variables.
- What it adds: endpoint normalisation, a bearer header from `PHOENIX_API_KEY`, the project name resource attribute and auto-instrumentation entry points. The project needs none except the project name, which is one line: `Resource.create({"openinference.project.name": "filingintel"})` (`openinference/semconv/resource/__init__.py`, `ResourceAttributes.PROJECT_NAME`; `otel.py` line 152 does the same).

Recommendation: do not depend on it.

### 2.5 Environment variables the SDK and exporter read

None of these is set by the project. Each one changes behaviour if a developer's shell sets it:

- `OTEL_SDK_DISABLED=true`: `TracerProvider.get_tracer` returns a `NoOpTracer` (`sdk/trace/__init__.py` lines 1278 to 1279; ran: `NoOpTracer`, nothing exported). The spec text is "Disable the SDK for all signals".
- `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`: read when no sampler is passed (`sdk/trace/sampling.py` lines 465 to 477). Pass `sampler=ALWAYS_ON` explicitly if a stray value should not drop spans.
- `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_SERVICE_NAME`, `OTEL_EXPERIMENTAL_RESOURCE_DETECTORS`: merged into the resource by `Resource.create` (`sdk/resources/__init__.py` lines 73 to 75, 333, 350).
- `OTEL_BSP_*`: batch processor sizes and timeouts (`sdk/trace/export/__init__.py` lines 27 to 30, 226 to 271).
- Exporter: `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_EXPORTER_OTLP_ENDPOINT` are used only when no `endpoint` argument is passed. `OTEL_EXPORTER_OTLP_TRACES_HEADERS` or `OTEL_EXPORTER_OTLP_HEADERS` are merged into the headers even when `headers` is passed. Also read: `…_TIMEOUT`, `…_COMPRESSION`, `…_CERTIFICATE`, `…_CLIENT_KEY`, `…_CLIENT_CERTIFICATE`, and a credential-provider variable (`trace_exporter/__init__.py` lines 32 to 41, 139 to 159; `_common/__init__.py` lines 129 to 166).
- `OTEL_PYTHON_TRACER_PROVIDER`: if set, `trace.get_tracer_provider()` loads a provider from an entry point instead of returning the proxy (`opentelemetry/trace/__init__.py` lines 564 to 572). With `opentelemetry-sdk` installed, `sdk_tracer_provider` is such an entry point, so this variable alone could create a recording provider. It still has no exporter, so it cannot send anything.
- `OTEL_TRACES_EXPORTER` (default `otlp` in the spec) is read only by the SDK's auto-configurator, `_OTelSDKConfigurator` in `sdk/_configuration/__init__.py`, which runs under `opentelemetry-instrument` or a distro. The project installs neither, so this cannot cause a surprise export.

Conclusion: plain SDK plus HTTP exporter cannot export unless the code builds an exporter. If the code builds one only when `PHOENIX_COLLECTOR_ENDPOINT` is set and passes `endpoint=` explicitly, no `OTEL_*` variable can turn exporting on. The one leak is `OTEL_EXPORTER_OTLP_HEADERS`, which would add headers to requests to the local Phoenix; harmless here.

## 3. OpenInference conventions

### 3.1 Span kinds and the mapping

`openinference.span.kind` values (`OpenInferenceSpanKindValues`, `openinference/semconv/trace/__init__.py` lines 651 to 663): `TOOL`, `CHAIN`, `LLM`, `RETRIEVER`, `EMBEDDING`, `AGENT`, `RERANKER`, `UNKNOWN`, `GUARDRAIL`, `EVALUATOR`, `PROMPT`, `DECISION`. The spec defines LLM as "a call to a Large Language Model", EMBEDDING as "a call to an LLM or embedding service", CHAIN as "a starting point or link between application steps", RETRIEVER as "a data retrieval step" and EVALUATOR as "a call to a function performing evaluation" (https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md).

| Step | Kind | Key attributes |
|---|---|---|
| Per-question root (`answer_question`) | CHAIN | `input.value` = question, `output.value` = answer status, `metadata` (question ID, arm, split, class) |
| Question filter | CHAIN | `input.value` = question; filter result in `output.value` as JSON with `output.mime_type` `application/json` |
| Query embedding (Voyage) | EMBEDDING | `embedding.model_name` = `voyage-4-large`, `embedding.embeddings.0.embedding.text` = question, cache hit, token count |
| Vector search (pgvector) | RETRIEVER | `input.value` = question, `retrieval.documents.{i}.document.id` / `.score` / `.metadata` |
| Answer generation | LLM | `llm.model_name`, `llm.provider` = `anthropic`, `llm.system` = `anthropic`, token counts, `llm.invocation_parameters` |
| Judge, per metric and run | EVALUATOR | metric name, run index, score |
| Judge API call (child of EVALUATOR) | LLM | `llm.model_name` = `gpt-6-luna`, `llm.provider` = `openai`, token counts including reasoning |

The judge calls must be LLM spans for Phoenix to compute their cost (section 3.4). GUARDRAIL would suit the decline path once the Step 13 router exists.

### 3.2 Attribute names

From `SpanAttributes` and related classes in `openinference/semconv/trace/__init__.py` (0.1.41); line numbers in brackets.

- Kind: `openinference.span.kind` [318]. Project: resource attribute `openinference.project.name` (`semconv/resource/__init__.py`).
- Input and output: `input.value` [25], `input.mime_type` [26], `output.value` [19], `output.mime_type` [20]. MIME values: `text/plain` or `application/json` [672 to 674].
- Model: `llm.model_name` [72]; also `llm.request.model_name` [76] and `llm.response.model_name` [82]. `llm.provider` [88] is "the provider of the model, such as OpenAI, Azure, Google". `llm.system` [92] is "the AI product as identified by the client or server". Enumerated values include `anthropic` and `openai` for both [677 to 707]. The spec says `llm.provider` names the hosting provider when it differs from the system, for example `azure` for Azure-hosted OpenAI. Here both are the same.
- Invocation parameters: `llm.invocation_parameters` [60], a JSON string, for example `{"effort": "high"}` or `{"reasoning": {"effort": "medium"}, "max_output_tokens": 10000}`. Embedding: `embedding.invocation_parameters` [46].
- Token counts: `llm.token_count.prompt` [184], `llm.token_count.completion` [172], `llm.token_count.total` [210], `llm.token_count.prompt_details.cache_read` [202], `.cache_write` [206], `.cache_input` [198], `llm.token_count.completion_details.reasoning` [180].
- Finish reason: `llm.finish_reason` [214].
- Messages (only when text capture is on): `llm.input_messages.{i}.message.role` / `.message.content`, and the same for `llm.output_messages` [64, 68, 388, 392].
- Prompt template: `llm.prompt_template.version` [168] can hold the answer prompt version (`v1`) and `llm.prompt_template.template` [160] its text.
- Retrieval: `retrieval.documents` [305], with each item's `document.id` [527], `document.score` [531], `document.content` [535] and `document.metadata` [539], a JSON string.
- Embedding: `embedding.model_name` [50], `embedding.embeddings` [42], with each item's `embedding.text` [578] and `embedding.vector` [582].
- Other: `metadata` [307] (a JSON string), `tag.tags` [313], `session.id` [320].
- Cost (not read by the Phoenix server, section 3.4): `llm.cost.prompt` [244], `llm.cost.completion` [219], `llm.cost.total` [277], plus details such as `llm.cost.prompt_details.cache_read` [263].

Lists are flattened with a zero-based index: "retrieval.documents.<index>.document.id" (spec). In code: `span.set_attribute("retrieval.documents.0.document.id", "0000950170-24-000000:0012")`. Tested with the in-memory exporter (ran).

### 3.3 Mapping our usage fields to token attributes

- Anthropic: in Anthropic's usage object, `input_tokens` excludes cache reads and writes, which are reported as `cache_read_input_tokens` and `cache_creation_input_tokens` (Anthropic API docs; not re-read for this note, unverified here). Phoenix computes input cost as `llm.token_count.prompt` minus the detail counts, priced at the input rate, plus each detail at its own rate (`cost_details_calculator.py` lines 119 to 183). So set `llm.token_count.prompt` to the sum of all three and the two details separately; otherwise the cached tokens are subtracted from a total that never included them.
- OpenAI Responses API: `input_tokens` includes `input_tokens_details.cached_tokens`, and `output_tokens` includes `output_tokens_details.reasoning_tokens` (luna note, `docs/research/openai-luna-judge.md`). Map `input_tokens` to `prompt`, `cached_tokens` to `prompt_details.cache_read`, `output_tokens` to `completion` and `reasoning_tokens` to `completion_details.reasoning`.
- Voyage: no `llm.token_count.*` on the EMBEDDING span (it is not an LLM span and Phoenix will not price it). Record the Voyage token count under a project attribute (section 3.4).

### 3.4 How Phoenix shows cost

- Phoenix computes cost on ingest. `should_calculate_span_cost` returns true only when the span kind is `LLM` and `llm.model_name` is a non-empty string (`phoenix/db/insertion/helpers.py` lines 104 to 114). The calculator then finds a price entry by regex match on `llm.model_name`, preferring entries whose provider matches `llm.provider` and user-defined entries over built-in ones (`server/cost_tracking/cost_model_lookup.py` lines 70 to 151), and prices the token counts (`cost_details_calculator.py`). The cost-tracking doc says the same: Phoenix needs `llm.model_name`, `llm.provider` and the `llm.token_count.*` attributes, and custom prices can be added under Settings → Models (https://arize.com/docs/phoenix/tracing/how-to-tracing/cost-tracking).
- The server never reads `llm.cost.*`. The only server-side match for `llm.cost` outside the UI bundle is `phoenix/experimental/datagen/replayer.py`. A cost attribute the project sends is stored as a plain attribute but does not feed Phoenix's cost totals.
- The built-in table (`server/cost_tracking/model_cost_manifest.json`, 401 entries, source "litellm") has:
  - `claude-sonnet-5`, pattern `claude-sonnet-5`, which also matches `claude-sonnet-5-5` (regex search). Prices: $2 input, $10 output, $0.20 cache read and $2.50 cache write per MTok. `retrieve/pricing.py` has `claude-sonnet-5-5` at $2.00 / $10.00, so they agree.
  - `gpt-6-luna`: $0.10 input, $0.50 output, $0.01 cache read and $0.125 cache write per MTok, doubling input above 272K prompt tokens. This matches the luna note's pricing table.
  - No `voyage` entry. Embedding spans are not priced anyway.
- Response cache: a cache hit makes no API call and costs nothing. If the span carries the recorded `llm.token_count.*`, Phoenix prices it as if it were a fresh call, so a fully cached rerun would show the full baseline cost (about $6.80). On a cache hit, set `filingintel.cache_hit = true` and leave out `llm.token_count.*` (store the recorded counts under `filingintel.cached_usage.*` if wanted).
- Recommendation: let Phoenix compute LLM cost from tokens, and also put the project's own figure from `retrieve/pricing.py` on every span that costs money, in a project-namespaced attribute such as `filingintel.cost_usd`, which also covers Voyage. Benchmark run files remain the source of truth for cost; Phoenix's totals are a convenience view.

### 3.5 Depend on `openinference-semantic-conventions` or hard-code?

- Package: version 0.1.41, uploaded 2026-10-01, Apache-2.0, `requires_python >=3.10,<3.15`, no dependencies (`requires_dist` is null), 12.7 KB wheel, 56 KB unpacked. Pure constants and enums.
- For depending: the names come from the spec's maintainers, typos fail at import rather than silently in the UI, and the cost is one lock entry with no transitive packages.
- Against: it is a 0.1.x package that releases often (0.1.41), and the project uses only about 25 of its names. Adding it still needs the user's approval.
- Recommendation: depend on it, pinned exactly (`openinference-semantic-conventions==0.1.41`), and import its constants in one `tracing` module. Fallback if the user declines: a single module of string constants copied from 0.1.41, with this note's line numbers as the reference.
- Do not take `openinference-instrumentation`. Its useful part for this project is the hide-input conventions (`OPENINFERENCE_HIDE_INPUTS`, `OPENINFERENCE_HIDE_OUTPUTS`, `OPENINFERENCE_HIDE_INPUT_MESSAGES`, `OPENINFERENCE_HIDE_EMBEDDING_VECTORS` and others, `openinference/instrumentation/config.py` lines 80 to 114). The project's "text off by default" setting can follow the same idea without the package. Note the default is reversed: OpenInference shows text unless hidden, while the project hides it unless enabled.

### 3.6 What to record and what not to

- Never set request headers, API keys, `Authorization` or the full client config as attributes. The SDK clients do not add spans themselves here, since no instrumentors are installed.
- Leave out `embedding.vector`: voyage-4-large vectors are large (1,024 or more floats per span, dimension not re-checked) and add nothing to debugging.
- `document.content` for retrieved chunks is public filing text, but it makes each RETRIEVER span about 10 chunks of text. Keep it behind the same text setting as prompts and answers.
- The question text is on by default (agreed). Questions are agent-drafted eval items and contain no personal data.
- OTel's default span limits apply: 128 attributes per span (`SpanLimits`; default from the OTel spec, not re-read). A RETRIEVER span with 10 documents and 3 attributes each, plus the rest, stays under 128. Unverified: the exact default was not re-read in 1.45.0.

## 4. Offline testing and the off switch

### 4.1 Asserting spans in pytest

`InMemorySpanExporter` (`opentelemetry/sdk/trace/export/in_memory_span_exporter.py`) has `get_finished_spans()`, which returns a tuple of `ReadableSpan`, and `clear()`. Pattern (ran):

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()

def test_vector_search_records_chunk_ids(spans):
    provider, exporter = spans
    run_vector_search(..., tracer=provider.get_tracer("filingintel"))
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "vector_search"]
    assert span.attributes["openinference.span.kind"] == "RETRIEVER"
    assert span.attributes["retrieval.documents.0.document.id"] == "..."
```

Use `SimpleSpanProcessor` in tests so spans are visible as soon as they end. `shutdown_on_exit=False` stops each test provider from adding an atexit handler.

### 4.2 The global provider can be set once

- `trace.set_tracer_provider()` goes through `_TRACER_PROVIDER_SET_ONCE = Once()`. A second call does nothing and logs "Overriding of current TracerProvider is not allowed" (`opentelemetry/trace/__init__.py` lines 515, 549 to 552; ran: the first provider stayed in place).
- A `ProxyTracer` obtained before the global is set starts delegating to the real provider once it is set (ran). So module-level `tracer = trace.get_tracer(__name__)` works in production, but tests cannot swap providers between tests through the global.
- OpenTelemetry's own test utilities (`opentelemetry-test-utils`) reset the private `_TRACER_PROVIDER_SET_ONCE` between tests. That relies on private API; unverified for 1.45.0 and not recommended.
- Recommendation: one small module owns tracing. It exposes `get_tracer()`, which returns the configured provider's tracer, or the API's no-op tracer when tracing is off, and a `configure(provider)` hook for tests. Instrumented functions take the tracer from that module, or as an argument where the function is already parameterised. Do not call `trace.set_tracer_provider` at all; nothing else in the process needs the global, since no auto-instrumentors are installed.

### 4.3 Off with zero network calls

- Import: importing `opentelemetry.sdk.trace`, `opentelemetry.sdk.trace.export`, the in-memory exporter and `opentelemetry.exporter.otlp.proto.http.trace_exporter` made no `socket.connect` or `create_connection` call (ran, with both patched to raise). It took 0.5 s and loaded `google.protobuf` but not `grpc` or `urllib3`. Constructing `OTLPSpanExporter` made no connection either; the first connection happens on export (ran, section 2.3).
- When `PHOENIX_COLLECTOR_ENDPOINT` is unset, the cheapest off mode is to build no SDK objects. `trace.get_tracer(...)` returns a `ProxyTracer`, and its spans are `NonRecordingSpan` with `is_recording()` false (ran). Setting attributes on them does nothing.
- Avoid building attribute values when not recording: wrap expensive work, such as JSON-encoding 10 documents, in `if span.is_recording():`.
- Lazy import: importing the exporter only inside the "on" branch keeps protobuf out of every CLI start when tracing is off.
- `OTEL_SDK_DISABLED=true` also disables an SDK provider (section 2.5). The project switch should be `PHOENIX_COLLECTOR_ENDPOINT` alone, as decided; honouring `OTEL_SDK_DISABLED` comes free if an SDK provider is built.
- pytest: `addopts = "--disable-socket --allow-hosts=localhost,127.0.0.1,::1"` lets tests reach localhost, and Phoenix listens on localhost. If a developer's shell or `.env` sets `PHOENIX_COLLECTOR_ENDPOINT`, `uv run --env-file .env pytest` would turn tracing on and export test spans to the local Phoenix. Add an autouse fixture in `tests/conftest.py` that runs `monkeypatch.delenv` on `PHOENIX_COLLECTOR_ENDPOINT` and the `OTEL_EXPORTER_OTLP_*` variables, plus one test that asserts the off path builds no exporter.
- CI sets none of these variables, so CI runs with tracing off.

### 4.4 Threads lose the parent span

OpenTelemetry stores the current span in `contextvars`, and `ThreadPoolExecutor.submit` does not copy the caller's context. A span started in a worker had `parent=None`; the same call through `contextvars.copy_context().run` had the root as parent (ran). `eval/judging/runner.py` (lines 13, 29 and 60) runs judges in a pool of 4 workers. Submit `ctx.run, fn, *args` with a fresh `copy_context()` per task, or pass the parent span context explicitly, or each judge call becomes its own trace in Phoenix.

## 5. GitHub required status checks

### 5.1 How the check name maps to the job

- GitHub Actions creates check runs, not commit statuses ("GitHub Actions generates checks, not commit statuses, when workflows are run", `content/pull-requests/reference/status-checks.md` line 43).
- `jobs.<job_id>.name` is "a name for the job, which is displayed in the GitHub UI" (`data/reusables/actions/jobs/section-using-jobs-in-a-workflow-name.md`). The check run name is that `name:`, or the job id when no name is set. Evidence: commit c7da22a has one Actions check run named `lint-and-test`, from app `github-actions` with integration ID 15368 (`gh api repos/Rohanvasudev1/fillingintel/commits/c7da22a/check-runs`). The `ci.yml` job has id `lint-and-test` and no `name:`.
- The workflow name (`name: CI`) is not part of the check name used for the requirement. The UI shows "CI / lint-and-test", but the required context is `lint-and-test`. Unverified from a doc; consistent with the check-run name above.
- Matrix jobs create one check per combination, named like `job (value)`. Unverified: no doc line was found; check the real names in the PR checks list before adding them. The current job has no matrix.
- "If you use branch protection rules that require specific status checks, make sure that job names are unique across all workflows. Using the same job name in multiple workflows can cause ambiguous status check results and block pull requests from being merged." (`about-protected-branches.md` line 31). If Step 6 adds an eval job in a second workflow, give it a distinct name.
- "A required status check must have completed successfully in the chosen repository during the past seven days" before it can be picked (`troubleshooting-required-status-checks.md`).

### 5.2 Skipped workflows and jobs

From `content/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks.md` ("Handling skipped but required checks"):

- A workflow skipped by path filtering, branch filtering or a skip instruction in the commit message: "Associated checks stay in a 'Pending' state and block merging". Advice: "Avoid requiring workflows that can be skipped." The worked example ends: "the pull request is blocked with 'Waiting for status to be reported.'"
- A job skipped by an `if:` conditional: "The job reports 'Success'". The reusable note adds: "It will not prevent a pull request from merging, even if it is a required check."
- A job that depends on a failed job "is skipped and may not block merging". Use `always()` with `needs` for required checks that depend on other jobs.
- Successful conclusions are `success`, `skipped` and `neutral`. Required checks must pass on the latest commit SHA.
- Rulesets add an optional "Status check timeout (minutes)", after which checks that have not reported are treated as failed (`available-rules-for-rulesets.md` line 65).

Consequence: `ci.yml` has no path filters today, so the required check always reports. If Step 6 wants to skip expensive work, for example an eval slice, on docs-only changes, do it with a job-level or step-level `if:` inside an always-triggered workflow, or keep the expensive job out of the required set.

### 5.3 `push` and `pull_request` together

- `ci.yml` uses `on: push` and `on: pull_request` with no branch filter. A PR from a branch in the same repository then starts two workflow runs: one for the push to the branch and one for the PR. Both produce a check run named `lint-and-test`.
- GitHub Docs list both `push` and `pull_request` as events whose job checks are evaluated for a PR (`troubleshooting-required-status-checks.md`, "Checks from some workflow jobs are not evaluated"). For two check runs with the same name from the same app on one PR, the docs found say nothing directly. The "unique job names" tip (section 5.1) warns of ambiguous results across workflows. Whether a duplicate in the same workflow ever blocks: unverified. In practice it costs two CI runs per PR push, two Postgres service containers, and two rows in the checks list.
- The PR run tests the merge commit; the push run tests the branch head. When the test merge commit has a status, that is what must pass (troubleshooting page, "Conflicts between head commit and test merge commit").
- Recommendation: `on: push: branches: [main]` plus `on: pull_request`. Feature-branch pushes then run CI once, through the PR, and `main` still runs after the merge.

### 5.4 Effect on direct pushes to `main`

- "After enabling required status checks, all required status checks must pass before collaborators can merge changes into the protected branch. After all required status checks pass, any commits must either be pushed to another branch and then merged or pushed directly to the protected branch." (`about-protected-branches.md` line 99). A rejected push looks like `GH006: Protected branch update failed … Required status check "ci-build" is failing` (troubleshooting page).
- So a new commit that has not yet passed CI on another branch cannot be pushed straight to `main`. The current "push to main after every ticket" habit (memory note: "Revisit at Step 6") stops working unless the pusher can bypass.

### 5.5 Rulesets or classic branch protection

- Availability: "Rulesets are available in public repositories with GitHub Free and GitHub Free for organizations" (`data/reusables/gated-features/repo-rules.md`). The repository is public and owned by a personal account (`gh repo view`: `visibility PUBLIC`, `isInOrganization false`). It currently has no rulesets and `main` is not protected (read-only API calls: `GET /rulesets` returned `[]`; `GET …/branches/main/protection` returned 404 "Branch not protected").
- Rulesets: "Anyone with read access to a repository can view its active rulesets"; enforcement can be switched (`active`, `evaluate`, `disabled`) without deleting; several rulesets can stack, and with branch protection "the most restrictive version of the rule applies" (`about-rulesets.md` lines 47 to 54 and "About rule layering"). For a portfolio repository, the public visibility of rules is a plus.
- Classic branch protection: by default "the restrictions of a branch protection rule don't apply to people with admin permissions to the repository"; the "Do not allow bypassing the above settings" option applies them to admins too. "Actors may only be added to bypass lists when the repository belongs to an organization." (`about-protected-branches.md` lines 33 to 37, 154 to 158).
- Ruleset bypass: eligible actors include "Repository admins, organization owners, and enterprise owners", roles, teams and apps, and a bypass actor can be limited to "For pull requests only" (`data/reusables/repositories/rulesets-bypass-step.md`, `rulesets-branch-tag-bypass-optional-step.md`). The API says "OrganizationAdmin is not applicable for personal repositories"; use `actor_type: RepositoryRole` (REST docs, below). By default a ruleset has no bypass actors, so it binds the owner too.
- Recommendation: a ruleset on `~DEFAULT_BRANCH` that requires `lint-and-test` from integration 15368, with `strict_required_status_checks_policy: false` (one developer, little benefit from forcing branches up to date). Add the repository admin role as a bypass actor in `pull_request` mode, so the owner can merge their own PR in an emergency but cannot push untested commits directly. Pair it with a "require a pull request" rule only if the user wants that discipline; without it, the owner can still push a commit that already passed on a branch.

### 5.6 `gh api` call shapes (not run)

Create a ruleset (REST "Create a repository ruleset", https://docs.github.com/en/rest/repos/rules?apiVersion=2022-11-28). `enforcement` is `disabled`, `active` or `evaluate`; `conditions.ref_name.include` accepts `refs/heads/main`, `~DEFAULT_BRANCH` or `~ALL`; each `required_status_checks` item has `context` (required) and `integration_id` (optional); `strict_required_status_checks_policy` is required; `bypass_mode` is `always`, `pull_request` or `exempt`.

```bash
gh api --method POST repos/Rohanvasudev1/fillingintel/rulesets --input - <<'JSON'
{
  "name": "main: CI must pass",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [ { "context": "lint-and-test", "integration_id": 15368 } ]
      }
    }
  ],
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request" }
  ]
}
JSON
```

`actor_id: 5` for the repository admin role is unverified: the REST page does not list role IDs. Create the ruleset in the web UI once, or read an existing one with `gh api repos/Rohanvasudev1/fillingintel/rulesets/<id>`, to confirm. Start with `"enforcement": "evaluate"` to watch the rule before enforcing it. Evaluate mode may need a paid plan; unverified.

Classic branch protection, for comparison (REST "Update branch protection", https://docs.github.com/en/rest/branches/branch-protection?apiVersion=2022-11-28). All four top-level keys are required and nullable; `contexts` is under a closing-down notice in favour of `checks`:

```bash
gh api --method PUT repos/Rohanvasudev1/fillingintel/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": { "strict": false, "checks": [ { "context": "lint-and-test", "app_id": 15368 } ] },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```

Changing only the checks later: `PATCH …/protection/required_status_checks`.

## Open questions

1. Dependencies: approve `opentelemetry-sdk==1.45.0`, `opentelemetry-exporter-otlp-proto-http==1.45.0` (10 new lock entries including `protobuf`), and `openinference-semantic-conventions==0.1.41` (one entry, no dependencies)? Recommendation: yes to all three; no to `arize-phoenix-otel` and the gRPC exporter.
2. Compose service: bind Phoenix to `127.0.0.1` only, and set `PHOENIX_TELEMETRY_ENABLED=false` and `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false`? Recommendation: yes. Separately, should Postgres and Neo4j also be bound to localhost? They currently listen on all interfaces. Recommendation: yes, as its own small change outside the tracing ticket.
3. Cached responses: on a cache hit, leave out `llm.token_count.*` so Phoenix does not price replayed answers? Recommendation: yes, and record `filingintel.cache_hit` on every LLM and EMBEDDING span.
4. Workflow after protection: move to a branch and PR per ticket, with the admin bypass limited to PRs? Recommendation: yes. The alternative, an admin bypass in `always` mode, keeps direct pushes but makes the required check advisory for the owner.
5. Ruleset or classic protection? Recommendation: ruleset, because its rules are visible to readers of a public portfolio repository and it can be put in evaluate mode first.
6. Triggers: change `on: push` to `on: push: branches: [main]` to stop duplicate runs on PR branches? Recommendation: yes. It changes CI, so it needs approval.
7. Judge threads: accept a small change in `eval/judging/runner.py` to submit tasks under `contextvars.copy_context().run` so judge spans nest under their question? Recommendation: yes; without it each judge call shows up as a separate trace.
