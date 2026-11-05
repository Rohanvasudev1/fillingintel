# Streamlit UI (Step 14)

Research note for Step 14, written 2026-10-19, before any UI code exists. Streamlit is already the chosen UI stack. This note covers the version and footprint, citation rendering, caching, testing, screenshots, an optional FastAPI layer and hosting. Sources: docs.streamlit.io (fetched 2026-10-19, docs version 1.66.0); the Streamlit repository at tag `1.66.0` (commit `59ee618b`), read with `gh api`; PyPI JSON metadata; the component repositories' READMEs and source; playwright.dev; psycopg and Neo4j docs. The dependency footprint was resolved with `uv pip compile` in a scratch directory, not in the project. Claims not confirmed from a primary source are marked "unverified".

## Summary

- Streamlit 1.66.0 (2026-10-14) is current. Apache-2.0, Python >= 3.11 (3.10 dropped in 1.66.0; 3.15 supported). Since 1.57.0 it runs on Starlette and Uvicorn, not Tornado. Against our `uv.lock` it adds 12 packages: streamlit, altair, pydeck, starlette, uvicorn, itsdangerous, python-multipart, jsonschema, jsonschema-specifications, referencing, rpds-py and narwhals. pandas, pyarrow, numpy, protobuf, pillow and websockets are already locked, at versions inside its ranges.
- Telemetry is on by default (`browser.gatherUsageStats = true`). The viewer's browser fetches `https://data.streamlit.io/metrics.json` and sends events to the URL it names. Set `[browser] gatherUsageStats = false` in `.streamlit/config.toml`, or `STREAMLIT_BROWSER_GATHER_USAGE_STATS=false`.
- Clickable citations with no JS build step: native widgets work. Use one `st.pills` or `st.segmented_control` (or a tertiary `st.button` per citation) keyed in session state, with the passage in a side column. Neither `st.markdown` nor `st.html` returns click events to Python. Custom Components v2 (`st.components.v2.component`, since 1.51.0) takes inline HTML, CSS and JS strings with no build step and sends state and trigger values back to Python. It allows clickable `[1]` markers inside the answer text.
- Escaping: `st.markdown` escapes HTML unless `unsafe_allow_html=True`. Markdown syntax in filing text (`|`, `*`, `[...]`, `:color[...]`) still renders. `st.html` sanitises with DOMPurify, ignores JS by default and is not iframed. For a highlighted span, `html.escape()` each part and wrap the cited part in `<mark>`. That keeps the filing text exact.
- Reruns: a widget click while an answer is being generated does not cancel the blocking API call. The rerun takes effect at the next `st.*` call, and that run's output is lost unless it was saved. The project's response cache makes a repeat free.
- AppTest runs offline in the pytest process. `from_function(..., kwargs=...)` passes real Python objects, so a fake backend can be injected. Custom components come back as `UnknownElement`, and `click()`/`set_value()` on them raise `AppTestError`. That favours native widgets for anything a test must click.
- Graph view: `st.graphviz_chart` takes a DOT string, renders in the browser with Graphviz WASM, needs no system Graphviz and returns no clicks. st-link-analysis (MIT, Cytoscape.js, v0.4.0, 2025-06) can return edge clicks. streamlit-agraph and streamlit-flow return clicked IDs. pyvis returns nothing.
- Community Cloud cannot reach laptop databases. A hosted demo needs managed Postgres with pgvector, Neo4j (our graph, about 8.3k nodes, fits AuraDB Free's 200k/400k limits) and API keys behind a cost guard, because a public app's URL works for anyone.

## 1. Version, licence, footprint, telemetry

- PyPI: `streamlit` 1.66.0, uploaded 2026-10-14, `License: Apache-2.0`, `Requires-Python >=3.11` (https://pypi.org/pypi/streamlit/json). Release notes for 1.66.0: "Due to end of life, Python 3.10 is no longer supported" and "Streamlit supports Python 3.15" (https://docs.streamlit.io/develop/quick-reference/release-notes).
- Server: 1.57.0, "Streamlit now uses Starlette/Uvicorn instead of Tornado" (https://docs.streamlit.io/develop/quick-reference/release-notes/2026). The PyPI metadata agrees: tornado is listed through 1.55.0 and absent from 1.58.0 on.
- Required dependencies in 1.66.0 include `pandas>=1.5.3,<4`, `pyarrow>=10.0.1,<27,!=25.0.0`, `protobuf>=5.26.1,<8`, `numpy<3`, `altair`, `pydeck`, `starlette`, `uvicorn` and `websockets`. `graphviz`, `plotly` and `matplotlib` are in the optional `charts` extra. The lock already has pandas 2.3.3, pyarrow 25.0.1 and protobuf 7.36.2. A standalone resolve picks pandas 3.0.6, but the lock's 2.3.3 satisfies the range. Whether `uv add` keeps every locked version is unverified, because it was not run against `uv.lock`.
- Config precedence, lowest to highest: `~/.streamlit/config.toml`, `$CWD/.streamlit/config.toml`, `STREAMLIT_*` env vars, CLI flags (https://docs.streamlit.io/develop/concepts/configuration/options). The project file is read from the directory `streamlit run` starts in.
- `browser.gatherUsageStats` defaults to `true` (`lib/streamlit/config.py` line 1333; https://docs.streamlit.io/develop/api-reference/configuration/config.toml). `frontend/app/src/MetricsManager.ts` sends only when it is true. Without a host config it fetches `DEFAULT_METRICS_CONFIG = "https://data.streamlit.io/metrics.json"` and posts events to the URL in that file. The docs do not say what the events contain.

## 2. Answers with clickable citations

Native widgets:
- `st.button` returns `True` only on the run after the click. `type="tertiary"` draws plain text, and `on_click` takes a callback (https://docs.streamlit.io/develop/api-reference/widgets/st.button). Buttons sit in their own layout slot, so they cannot sit inline inside a paragraph.
- `st.pills(label, options, selection_mode="single", format_func=..., key=..., on_change=...)` returns the selected option. With a key, the value persists across reruns and can be set through `st.session_state[key]` (https://docs.streamlit.io/develop/api-reference/widgets/st.pills). One pills row of `[1] [2] [3]` under the answer, driving a passage panel in `st.columns`, needs no custom code.
- `st.popover` computes its content even when closed, unless state is tracked with a key and `on_change`. The docs advise against nesting popovers (https://docs.streamlit.io/develop/api-reference/layout/st.popover). An `st.expander` per citation is the simplest form, but the passage then shows below the answer, not beside it.

Text elements:
- `st.markdown`: "If this is False (default), any HTML tags found in body will be escaped." Markdown still applies, including directives such as `:orange-background[...]` (https://docs.streamlit.io/develop/api-reference/text/st.markdown). Filing text holds `|` tables and `*` and `[` characters. Rendering it as Markdown can change what the reader sees, so the passage needs escaping or a different element.
- `st.html`: "We sanitize HTML with DOMPurify". JavaScript is ignored unless `unsafe_allow_javascript=True` (since 1.52.0). Content "is not iframed". There is no height parameter (https://docs.streamlit.io/develop/api-reference/text/st.html). It returns nothing to Python. Escaping each text part with `html.escape` and wrapping the cited span in `<mark>` gives exact text with a highlight. DOMPurify is a second layer, not the escaping.
- `st.components.v1.html` renders in an iframe and was deprecated in 1.56.0 (https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v1.html).

Custom components:
- v1 `declare_component` runs in an iframe with a single return value (https://docs.streamlit.io/develop/api-reference/custom-components). Its usual form is a built React frontend.
- v2: `st.components.v2.component(name, *, html=None, css=None, js=None, isolate_styles=True)`. Each of `html`, `css` and `js` is a raw string or a path (https://docs.streamlit.io/develop/api-reference/custom-components/st.components.v2.component). It mounts in a shadow root, not an iframe. `isolate_styles` "is style isolation only, not a security boundary". The JS default export receives `data`, `parentElement`, `setStateValue` and `setTriggerValue` (https://docs.streamlit.io/develop/concepts/custom-components/components-v2/register). State values persist across reruns. Trigger values last one rerun and "Reset to `None` after the rerun completes". Callbacks are registered as `on_<key>_change` (https://docs.streamlit.io/develop/concepts/custom-components/components-v2/state-and-triggers). Inline components need no build step. Package-based ones use the npm `@streamlit/component-v2-lib`. A v2 component could render the answer with clickable inline markers. Its JS would then build the DOM from text, so it would need `textContent`, not `innerHTML`, to stay safe.
- Of these options, native widgets, `st.html` and inline v2 components need no build step. Only v1 and package-based v2 components need one.

## 3. Caching, session state and long calls

- `st.cache_resource(ttl, max_entries, show_spinner, validate, hash_funcs, on_release, scope="global"|"session", ...)`. `validate` is called on every access and must return a bool; `False` rebuilds the value. "Global resources must be thread-safe." Values are shared across sessions and not copied. `on_release` "isn't guaranteed to be called when an app is shut down" (https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_resource).
- `st.cache_data` pickles and returns a copy on each call. The caching guide recommends caching database queries with a `ttl`. A parameter with a leading underscore is not hashed (https://docs.streamlit.io/develop/concepts/architecture/caching).
- psycopg: "Connection objects are thread-safe", but only one cursor runs a query at a time, and cursors are not thread-safe (https://www.psycopg.org/psycopg3/docs/advanced/async.html). A `ConnectionPool` with `check=ConnectionPool.check_connection` handles dropped connections (https://www.psycopg.org/psycopg3/docs/advanced/pool.html). The Neo4j driver is thread-safe and sessions are not (docs/research/neo4j-driver-and-constraints.md). A cached global driver plus one session per call fits that.
- `st.status(label, expanded, state="running"|"complete"|"error")` with `.update()`. All content is sent even when collapsed (https://docs.streamlit.io/develop/api-reference/status/st.status).
- Reruns: `ScriptRunner.request_rerun` says the runner "will handle the rerun request as soon as it reaches an interrupt point". `_enqueue_forward_msg` checks for a pending request on each `st.*` call, and "Cooperative stop ... only takes effect once the script reaches an st.* call" (`lib/streamlit/runtime/scriptrunner/script_runner.py` lines 127–134, 317–329, 506–519 at 1.66.0). A 3–16 s answer call finishes, its result is discarded at the next `st.*` call, and the new run starts. Keep the last answer in `st.session_state`, and use `st.form` or `st.chat_input` so typing does not rerun.

## 4. Graph views

| Option | Licence | Latest release | Activity | Clicks back to Python |
|---|---|---|---|---|
| `st.graphviz_chart` | Apache-2.0 (Streamlit) | 1.66.0 | core | none documented |
| streamlit-agraph (vis.js) | MIT (repo; PyPI field empty) | 0.0.45, 2023-01-28 | last push 2026-01-25 | clicked node ID, or edge ID if no node is selected (`StreamlitVisGraph.tsx`) |
| st-link-analysis (Cytoscape.js) | MIT | 0.4.0, 2025-06-26 | last commit 2025-06-26 | `Event(name, "click tap", "edge")` returns `{action, data}` (`component/events.py`) |
| pyvis via HTML | BSD-3-Clause | 0.3.2, 2023-02-24 | last push 2024-04 | none; iframe, and `components.v1.html` is deprecated; pulls ipython |
| streamlit-flow (React Flow) | MIT (repo; PyPI field empty) | 1.6.1, 2025-02-28 | last push 2025-06-24 | "clicks on nodes and edges can be captured"; state must live in `session_state` |

`st.graphviz_chart` accepts a DOT `str`. `import graphviz` sits under `TYPE_CHECKING` only (`lib/streamlit/elements/graphviz_chart.py` lines 34, 232), and rendering uses "d3-graphviz library and Graphviz WASM" (https://docs.streamlit.io/develop/api-reference/charts/st.graphviz_chart). The three component packages are v1 iframes, and AppTest cannot click them. Sources: PyPI JSON for each; `gh api repos/{ChrisDelClea/streamlit-agraph, AlrasheedA/st-link-analysis, WestHealth/pyvis, dkapur17/streamlit-flow}`.

## 5. Testing with AppTest

- `AppTest.from_file(path, *, default_timeout=3)`, `from_string`, and `from_function(script, *, default_timeout=3, args=None, kwargs=None)`. `run(timeout=None)` (https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest). `from_function` runs the function's source with the given `args`/`kwargs` in-process (`lib/streamlit/testing/v1/app_test.py` lines 374–421). It runs inside pytest, so pytest-socket's rules apply.
- Session state and `at.secrets[...]` can be set before the first run. One AppTest tests one page (https://docs.streamlit.io/develop/concepts/app-testing/beyond-the-basics).
- Supported elements include `button`, `pills`, `segmented_control`, `chat_input`, `form`, `html`, `markdown`, `status`, `expander` and `columns` (`element_tree.py` parse table). Other types become `UnknownElement`. Since 1.64.0, `set_value()` or `click()` on one "raises `AppTestError`" (2026 release notes). 1.66.0 "renders imported Custom Components v2". Rendering is not interacting. Whether a v2 value can be injected through session state is unverified.

## 6. README screenshot

- Playwright for Python 1.64.0 (2026-10-13, Apache-2.0, Python >= 3.10) (https://pypi.org/pypi/playwright/json). Browsers are a separate download into `~/Library/Caches/ms-playwright`, "a few hundred megabytes", and `--only-shell` skips full Chromium for headless use (https://playwright.dev/python/docs/browsers). `page.screenshot(path=..., full_page=True)` and `locator.screenshot(...)` (https://playwright.dev/python/docs/screenshots). The `animations` and `mask` options are in the API reference; they were not confirmed here.
- Wait condition: Streamlit's own e2e suite waits for `[data-testid='stApp'][data-test-script-state='notRunning']` (`e2e_playwright/conftest.py` lines 1639–1690 at 1.66.0). This is a test hook, not a documented API.
- For a reproducible image, run the app headless (`server.headless=true`) on cached answers with dummy keys, so a cache miss fails rather than spends.

## 7. FastAPI between UI and retrieval

Streamlit scripts run as Python on the server, so the UI can import the retrieval package and call it directly. Direct calls cost nothing extra, and they share the process, caches and database drivers. One `streamlit run` serves the app. AppTest can inject a fake backend object.

An HTTP layer adds a second process, request/response schemas, timeouts and error mapping, and an HTTP client dependency (httpx is already used). In return the backend becomes testable without Streamlit, usable by other clients, and deployable apart from the UI. Since 1.57.0, `st.App(script_path, secrets=..., lifespan=..., routes=..., middleware=...)` can mount Starlette routes "alongside Streamlit" in the same server (`lib/streamlit/web/server/starlette/starlette_app.py` lines 296–336). That is a middle option. FastAPI's docs were not consulted; FastAPI is not a dependency today.

## 8. Hosting

- Community Cloud: CPU 0.078–2 cores, memory 690 MB–2.7 GB, storage up to 50 GB ("may change at any time without notice"). Apps sleep after 12 hours without traffic (https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app). Each account gets one private app. An app from a public repo is public, and anyone with the URL can use it (https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app). Secrets are pasted as `secrets.toml` in settings (https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management). It reads `uv.lock` first among dependency files (https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).
- The app runs on Streamlit's servers, so `127.0.0.1` Postgres and Neo4j are unreachable. A hosted demo needs managed Postgres with pgvector, about 2,144 chunks and their vectors. It also needs a reachable Neo4j. AuraDB Free allows 200,000 nodes, 400,000 relationships and 1 GB (https://neo4j.com/docs/aura/quotas-and-limits/). Load run 6 has about 8.3k nodes (`benchmarks/graph/load-run6.json`). Free-tier pausing rules were not found in Neo4j's docs (unverified).
- Keys: a public app spends the owner's Anthropic, OpenAI and Voyage keys on every question asked. Options are a private app, a replay-only mode on cached questions, or per-session and daily spend caps in code.

## Not verified

The contents of Streamlit usage events. Whether `uv add streamlit` keeps every locked version. Setting v2 component values in AppTest. Playwright's `animations` and `mask` defaults. AuraDB Free pausing rules. FastAPI docs.
