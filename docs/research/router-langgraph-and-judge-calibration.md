# Router, LangGraph and judge calibration (Step 13)

Research note for Step 13, written 2026-10-16 during the grilling, before any router or calibration code exists. It answers five questions: whether LangGraph fits a small router, how its runs interact with our hand-written OpenTelemetry spans, what it adds over a plain dispatch function, how published systems build and report query routers (with Claude Haiku 5.5's effort levels and prices), and how to calibrate our LLM judges against human scores.

Sources: PyPI JSON metadata (`pypi.org/pypi/<name>/json`, fetched 2026-10-16); the installed wheels in the main checkout's `.venv` (`langgraph` 1.2.12, `langgraph-checkpoint` 4.2.0, `langgraph-sdk` 0.4.5, `langchain-core` 1.6.6, `langsmith` 0.14.4), read directly; `uv.lock` in this worktree; the LangGraph and LangSmith docs at docs.langchain.com; Anthropic's docs at platform.claude.com (pricing and effort pages, fetched 2026-10-16); the LlamaIndex and Microsoft GraphRAG docs; and the papers cited by arXiv ID. Claims marked "(ran)" were checked by running a throwaway script with the main checkout's interpreter (`.venv/bin/python -I`), with `socket.connect` patched to raise. Nothing in the project was changed. Anything not confirmed from a primary source is marked "unverified".

## Summary

- LangGraph is already installed. `ragas==0.4.3` depends on `langchain` 1.4.3, which depends on `langgraph`, so `uv.lock` already pins `langgraph` 1.2.12, `langgraph-checkpoint` 4.2.0, `langgraph-prebuilt` 1.1.0, `langgraph-sdk` 0.4.5, `langchain-core` 1.6.6 and `langsmith` 0.14.4. Making `langgraph==1.2.12` a direct dependency adds no package and changes no version (section 1.2).
- 1.2.12 (2026-09-21) is the newest release at least two weeks old. 1.2.13 to 1.2.15 (2026-10-05 to 10-12) are too new, and 1.2.15 needs `langchain-core>=1.4.7` and `langgraph-checkpoint>=4.3.0`, which would move locked versions (section 1.1).
- Importing and running a compiled graph made no network call and read no `.env` (ran). LangSmith tracing is off unless `LANGSMITH_TRACING` (or `LANGCHAIN_TRACING_V2`) is `true`. `langsmith` registers a pytest plugin, but it is already installed today through ragas, and it does nothing unless a test carries `@pytest.mark.langsmith` (section 1.3).
- LangGraph emits no OpenTelemetry spans of its own. Hand-written spans nest correctly under a caller's span: a single node runs in the caller's thread, and parallel nodes run in a thread pool under `copy_context()`, so the parent is kept in sync `invoke`, parallel `invoke` and `ainvoke` (ran, section 2).
- For a classify-then-dispatch router, LangGraph buys a Mermaid diagram and little else. Checkpointing, streaming and retries are either off by default or unwanted. For Step 13b's tool-calling arms it offers a loop with `recursion_limit`, but a hand-written loop with a call cap is about as short (section 3).
- Published routers (Adaptive-RAG, LlamaIndex's router, RouteLLM) report accuracy with a confusion matrix and per-class accuracy, and judge the router by the downstream answer quality, compared against fixed arms and an oracle (section 4).
- Claude Haiku 5.5 accepts effort `low`, `medium`, `high`, `xhigh` and `max`, default `medium`. Prices for prompts up to 100K tokens: $0.10 input, $0.50 output, $0.125 5-minute cache write, $0.01 cache read per MTok. Structured outputs are supported, and the project already uses them on Haiku 5.5 (section 4.4).
- Calibration: Cohen's kappa suits the binary judgments only. For the continuous Ragas scores use Spearman's rho (or Krippendorff's alpha, interval) plus mean absolute error and the mean signed difference. With 60 items a kappa of 0.7 carries a 95% interval of about ±0.18, and a skewed item set (nearly all declines correct) makes kappa unstable (section 5).

## 1. LangGraph as a dependency

### 1.1 Version and release dates

From PyPI JSON, fetched 2026-10-16:

| Package | Latest | Recent releases (upload date) |
|---|---|---|
| langgraph | 1.2.15 | 1.2.15 (2026-10-12), 1.2.14 (10-06), 1.2.13 (10-05), 1.2.12 (09-21), 1.2.11 (08-11) |
| langgraph-checkpoint | 4.3.0 | 4.3.0 (10-12), 4.2.0 (08-07) |
| langgraph-prebuilt | 1.1.1 | 1.1.1 (10-15), 1.1.0 (05-12) |
| langgraph-sdk | 0.4.7 | 0.4.7 (10-12), 0.4.6 (10-06), 0.4.5 (09-21) |
| langchain-core | 1.6.11 | 1.6.11 (10-15) ... 1.6.6 (09-29) |

Two weeks before 2026-10-16 is 2026-10-02, so 1.2.12 is the newest `langgraph` that qualifies. `langgraph` requires Python >=3.10.

1.2.15's metadata requires `langchain-core<2,>=1.4.7`, `langgraph-checkpoint<5,>=4.3.0`, `langgraph-prebuilt<1.2,>=1.1.0`, `langgraph-sdk<0.5,>=0.4.6`, `pydantic>=2.7.4` and `xxhash>=3.5.0`. Moving to it would bump three locked packages; there is no reason to.

### 1.2 Dependency tree and what is already locked

`uv.lock` in this worktree (read 2026-10-16) has this chain: `ragas` 0.4.3 → `langchain` 1.4.3 → `langchain-core`, `langgraph`, `pydantic`. Under `langgraph` 1.2.12:

- `langchain-core` 1.6.6 → `httpx`, `jsonpatch`, `langchain-protocol`, `langsmith`, `packaging`, `pydantic`, `pyyaml`, `tenacity`, `typing-extensions`, `uuid-utils`
- `langgraph-checkpoint` 4.2.0 → `langchain-core`, `ormsgpack` 1.12.2
- `langgraph-prebuilt` 1.1.0 → `langchain-core`, `langgraph-checkpoint`
- `langgraph-sdk` 0.4.5 → `httpx` 0.28.1, `langchain-core`, `langchain-protocol`, `orjson` 3.12.0, `websockets`
- `pydantic` 2.13.5, `xxhash` 3.8.1
- `langsmith` 0.14.4 → `orjson`, `packaging`, `pydantic`, `requests`, `requests-toolbelt`, `sniffio`, `typing-extensions`, `uuid-utils`, `xxhash`, `zstandard`

All of these are in the lock and installed in `.venv` today. Declaring `langgraph==1.2.12` in `pyproject.toml` therefore changes nothing in `uv.lock` beyond marking it direct (not verified with `uv add --no-sync`; worth one scratch run before the change). The `langchain-community<0.4.2` constraint is unaffected, since `langgraph` does not depend on `langchain-community`.

`langgraph-sdk` is the client for a remote LangGraph Server; `langgraph/pregel/remote.py` imports it and `langsmith`. A local `StateGraph` does not use it.

### 1.3 Network, telemetry, `.env`, environment variables, pytest

- Network (ran): a script that patched `socket.socket.connect` to raise, imported `langgraph.graph`, compiled a `StateGraph` with a conditional edge, invoked it twice and drew its Mermaid text made zero connection attempts. `langsmith` was imported as a side effect.
- Telemetry: no file under `langgraph/` or `langgraph_sdk/` mentions telemetry (grep of the installed wheels). LangSmith is the only outbound path.
- LangSmith tracing: `langsmith/utils.py::tracing_is_enabled` returns true only when a tracing context enables it, a run tree is active, a global fallback is set, or the environment variable `LANGSMITH_TRACING_V2`/`LANGSMITH_TRACING` (or the `LANGCHAIN_` forms) equals `"true"`. Nothing in our code sets these, so tracing stays off with no LangSmith variables at all. To be explicit, a router test can assert that `LANGSMITH_TRACING` is unset, as the autouse fixture already does for `PHOENIX_COLLECTOR_ENDPOINT`.
- `.env`: no `load_dotenv` or `dotenv` reference in `langgraph`, `langgraph_sdk`, `langsmith` or `langchain_core` (grep). Invariant 7 is safe.
- Environment variables LangGraph itself reads: `LANGGRAPH_DEFAULT_RECURSION_LIMIT` and `LANGGRAPH_DELTA_MAX_SUPERSTEPS_SINCE_SNAPSHOT` (`langgraph/_internal/_config.py`), `LANGGRAPH_STRICT_MSGPACK` and `LANGGRAPH_AES_KEY` (checkpoint serde). None is needed.
- Network in a helper: `langchain_core/runnables/graph_mermaid.py` renders PNGs through the public `https://mermaid.ink` service by default. `draw_mermaid()` (text) is offline; `draw_mermaid_png()` is not and must not be used in tests or CI.
- pytest plugin: `langsmith-0.14.4.dist-info/entry_points.txt` registers `[pytest11] langsmith_plugin = langsmith.pytest_plugin`. It adds a `--langsmith-output` option and wraps only tests marked `@pytest.mark.langsmith` (`pytest_runtest_call`). It is loaded in every pytest run today, since ragas brought `langsmith` in. `-p no:langsmith_plugin` in `pyproject.toml`'s pytest `addopts` would remove it; harmless either way. `langgraph` itself registers no entry points.

## 2. Tracing: LangGraph and our OpenTelemetry spans

### 2.1 What LangGraph emits

LangGraph has no OpenTelemetry code (grep of `langgraph/` for `opentelemetry`: no match). Its tracing goes through LangChain callbacks to LangSmith. LangSmith can forward to an OTel endpoint: the docs say to set `LANGSMITH_OTEL_ENABLED=true` with `LANGSMITH_TRACING=true`, install `langsmith[otel]`, and `LANGSMITH_OTEL_ONLY=true` to skip LangSmith's own backend (https://docs.langchain.com/langsmith/trace-with-opentelemetry). That path sets global OTel state and pulls an extra; it does not fit our rule that the caller passes its own provider. OpenInference's auto-instrumentation `openinference-instrumentation-langchain` 0.1.79 (2026-10-09) exists but is a new dependency with `wrapt` and `opentelemetry-instrumentation`, and is too new under the two-week rule anyway.

So router spans stay hand-written in `retrieve/tracing.py`, as for every other arm: a `route` (CHAIN) span with the predicted class, the arm chosen and the classifier call as a child LLM span.

### 2.2 Context propagation (ran)

`langgraph/pregel/_runner.py::PregelRunner.tick` runs a step with one task inline, in the caller's thread ("fast path if single task with no timeout and no waiter"). A step with several tasks submits them through `langgraph/pregel/_executor.py::BackgroundExecutor.submit`, which calls `copy_context()` and runs each task under `ctx.run`. Either way the caller's `contextvars`, and so the current OTel span, reach the node.

A test with a `TracerProvider` built locally (never set globally), a `SimpleSpanProcessor(InMemorySpanExporter())` and two nodes opening spans with `tracer.start_as_current_span`:

- sync `invoke`, one node per step: node span's parent is the caller's root span, and the node ran on the caller's thread;
- sync `invoke`, two nodes in one step (thread pool): both node spans have the root as parent;
- `ainvoke` under `asyncio.run`: both node spans have the async root as parent.

The router can therefore pass our tracer into node functions (by closure or through the state) and open spans inside them, with no change to `retrieve/tracing.py`. Sync `invoke` is enough: every arm is synchronous today.

## 3. What LangGraph adds over a dispatch function

The Step 13 router is: classify the question (one Haiku call), then call one arm or return a decline. As a plain function that is a `match` on an enum. As a graph it is `StateGraph(State)`, a `classify` node, `add_conditional_edges("classify", pick)` where `pick` returns an arm node name or `END`, and one node per arm (ran; the graph compiled and routed both ways).

What the graph adds, checked against the installed source:

- Checkpointing: off unless `compile(checkpointer=...)` is passed. A one-pass router has nothing to resume. Not needed.
- Streaming: `stream()` yields state updates per node. The harness records per-stage cost and latency itself. Not needed.
- Retries: `add_node(..., retry_policy=RetryPolicy(...))`; `RetryPolicy` defaults to 3 attempts with jittered backoff (`langgraph/types.py`). No retry applies unless a policy is given. Our API clients already retry, the response cache makes reruns free, and the Cypher arm deliberately has "no automatic retry". Not wanted.
- Visualisation: `app.get_graph().draw_mermaid()` gives a Mermaid flowchart offline (ran). Useful for the README and Step 14's "reasoning path".
- Cost: it puts LangChain's runnable config, callbacks and a Pregel loop between the harness and each arm. Errors from an arm surface through Pregel's commit path; stack traces get longer.

For this router LangGraph adds a diagram and little else. A dispatch function is shorter, easier to test, and keeps the arms' existing error handling (for instance, a lost Neo4j connection stops the run).

Step 13b's agentic arms are where a graph earns more. LangGraph gives a tool-calling loop as a cycle (`agent` node, `tools` node, conditional edge back), per-thread state, optional checkpointing for long runs, and `recursion_limit`, a hard cap on supersteps that raises `GraphRecursionError`. Two cautions from the source:

- The default limit is large. The Graph API page says "Starting in version 1.0.6, the default recursion limit is set to 1000 steps" (https://docs.langchain.com/oss/python/langgraph/graph-api), but 1.2.12's `_config.py` sets `DEFAULT_RECURSION_LIMIT = int(getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "10007"))`, and a looping test graph stopped at 10,007 (ran). An agentic arm must pass `{"recursion_limit": N}` in the config every time.
- `recursion_limit` counts supersteps, not tool calls. One agent turn plus one tool turn is two steps, and parallel tool calls in one turn count once. A cap on tool calls (as Step 13b plans) still needs a counter in state or in the tool node; a hand-written loop counts calls directly.

The Anthropic SDK's tool runner and a manual `while stop_reason == "tool_use"` loop are the alternatives; either gives a call cap in a few lines and keeps the response cache in front of every call. Whether LangGraph is worth it for 13b is a judgment for that step; for Step 13 it is not needed.

## 4. Query routers for RAG

### 4.1 Published practice

- Adaptive-RAG (Jeong et al., NAACL 2024, arXiv 2403.14403): a T5-Large classifier picks among no retrieval, single-step and multi-step retrieval. Labels are "silver": a query takes the simplest strategy that answered it correctly, with dataset origin as a tie rule. Training used 400 queries per dataset, kept apart from test queries. The paper reports overall accuracy (54.5%), per-class accuracy (30.5%, 66.3%, 65.5%) and a confusion matrix, then shows that end-to-end QA quality and time per query matter more than classifier accuracy: routing helps even at that accuracy because the costly confusions are rare.
- LlamaIndex `RouterQueryEngine` (https://developers.llamaindex.ai/python/framework/module_guides/querying/router/): each engine is a tool with a written description; `LLMSingleSelector` puts the choices in a text prompt, `PydanticSingleSelector` uses function calling and returns a typed object. The selector sees descriptions, not examples.
- Microsoft GraphRAG (https://microsoft.github.io/graphrag/query/overview/): local, global, DRIFT and basic search are separate methods with written guidance (local "for questions that require an understanding of specific entities", global "for questions that require an understanding of the dataset as a whole"). The docs describe no automatic router; the caller chooses.
- RouteLLM (Ong et al., arXiv 2406.18665) routes between a strong and a weak model, trained on preference data, and reports cost against quality. It is a different problem (which model, not which retriever), but its framing carries over: report the router against always-strong and always-weak baselines and against a random router at the same mix.

### 4.2 Design choices this supports

- Output: structured output with an enum of the classes (lookup, local, multi_hop, global, plus decline), a short reason field before the label, and nothing free-form. The class-to-arm map stays in code (RESEARCH-PLAN's map), so the model never names an arm.
- Few-shot examples: invented, never eval questions, as the Cypher prompt already does. Using eval questions as examples leaks gold classes into the measured set, the same failure ADR-0001 rules out for filters. LlamaIndex shows descriptions alone can work; 1–2 invented examples per class is a reasonable start.
- Input: question text only, never eval labels (ADR-0001's rule extended to the router).
- Decline: the router's decline output and the answer prompt's decline status are two separate chances to refuse advice. Count them separately.

### 4.3 How to report router accuracy

- A confusion matrix over gold class × predicted class, on `dev` while iterating and on `test` once, plus per-class precision, recall and accuracy. With about 10 questions in some classes, per-class rows are "directional" under RESEARCH-PLAN's rule.
- Overall accuracy with a fixed-seed bootstrap interval, and Cohen's kappa against the gold labels, since class sizes are unequal.
- The downstream figure that matters: the routed system's metrics against each fixed arm and against an oracle router that uses the gold class. Routed minus oracle is what misrouting costs; routed minus best fixed arm is what routing buys. Adaptive-RAG's lesson is that a router with modest accuracy can still win here.
- The gold classes are agent-drafted labels, so router accuracy is "against agent-drafted class labels", not against human ones.

### 4.4 Claude Haiku 5.5

- Model ID `claude-haiku-5-5`, 1M context, 128K output (Anthropic models overview, via the bundled claude-api reference dated 2026-10-06).
- Effort: the effort page (https://platform.claude.com/docs/en/build-with-claude/effort, fetched 2026-10-16) lists `claude-haiku-5-5` as supported, with all five levels (`low`, `medium`, `high`, `xhigh`, `max`), and says "Claude Opus 5.5 and Claude Haiku 5.5 default to medium". Its Haiku guidance: `low` for "chat, short tool tasks, and simple, high-volume requests", `medium` for most work, `high` for "knowledge work, longer agent tasks, and strict instruction following". Thinking is on by default and counts toward `max_tokens`; `thinking: {"type": "disabled"}` is accepted at `high` or below and returns 400 at `xhigh`/`max`. Non-default `temperature`/`top_p`/`top_k` return 400.
- Prices (https://platform.claude.com/docs/en/about-claude/pricing, fetched 2026-10-16), per MTok, prompts up to 100,000 tokens: input $0.10, 5-minute cache write $0.125, 1-hour cache write $0.20, cache hit $0.01, output $0.50. Over 100,000 tokens: $0.50, $0.625, $1, $0.05, $2.50. Batch: $0.05 / $0.25. These match `retrieve/pricing.py`'s `TABLE_2026_10_10` entry. The minimum cacheable prompt length for Haiku 5.5 was not checked; a short router prompt may not cache, which matters little at these prices.
- Structured outputs: the bundled claude-api reference lists Claude Haiku 5.5 among models supporting structured outputs (`output_config.format`). The project already sends structured output to Haiku 5.5 in `extract/request.py`, `resolve/request.py`, `persist/request.py` and `graph/report_request.py`.
- Cost for this router: a prompt of about 1,500 tokens (schema text not needed, only class descriptions and examples) and an output of a few hundred tokens with thinking at `low` costs roughly $0.0003 per question, under $0.05 for all 140 questions (estimate, not measured). Effort `low` versus `medium` is worth a dev comparison, since the docs place classification at `low`.

## 5. Calibrating LLM judges against human scores

### 5.1 What we have to calibrate

- Continuous 0–1 scores: Ragas faithfulness (supported statements over all statements) and answer relevancy, each run 3 times; the report shows the mean.
- Binary or categorical judgments: citation support, decline correctness, not-found correctness.

RESEARCH-PLAN line 40 and OPEN-DECISIONS say "Cohen's kappa ≥ 0.6 against ~60 hand-scored answers", and below 0.6 the judged faithfulness column is dropped.

### 5.2 What the literature uses

- Ragas paper (Es et al., arXiv 2309.15217): WikiEval, 50 Wikipedia pages, two annotators (about 95% agreement on faithfulness, 90% on answer relevance). Agreement was measured pairwise: humans chose the better of two answers and the metric was scored on whether it agreed; faithfulness 0.95, answer relevance 0.78, context relevance 0.70. This is accuracy on easy contrasts (an answer written with context against one without), not agreement on absolute scores, so it does not tell us how well a 0.73 matches a human's 0.73. Faithfulness is defined as |V|/|S| over LLM-extracted statements S.
- Ragas docs, "Align LLM as judge" (https://docs.ragas.io/en/latest/howtos/applications/align-llm-as-judge/, dated 2025-12-11): binary pass/fail labels, alignment reported as the match rate, and "a representative sample (100-200 examples covering diverse scenarios) is sufficient for reliable alignment". No train/test split for prompt iteration.
- G-Eval (Liu et al., arXiv 2303.16634): continuous judge scores compared with human ratings by Spearman's rho and Kendall's tau at summary level on SummEval and Topical-Chat.
- MT-Bench / "Judging LLM-as-a-judge" (Zheng et al., arXiv 2306.05685): pairwise preferences, reported as percentage agreement with humans, with human–human agreement as the reference point.
- "Judging the Judges" (Thakur et al., arXiv 2406.12624, v6 2025-08-18): percent agreement misleads ("judges with high percent agreement can still assign vastly different scores"); the authors report Scott's pi with percent agreement and dropped Cohen's kappa in later versions on theoretical grounds. Recommendation: report both, plus a qualitative error analysis.
- ARES (Saad-Falcon et al., NAACL 2024, arXiv 2311.09476): uses "a few hundred human annotations" with prediction-powered inference to put confidence intervals on judge-based scores. A route to corrected, not just checked, scores if we ever need it.
- FActScore (Min et al., arXiv 2305.14251): humans split a generation into atomic facts and label each supported, not supported or irrelevant against a knowledge source; the score is the supported share. This is the human counterpart of Ragas faithfulness.

### 5.3 Which statistic for which score

| Score | Human label | Primary statistic | Also report |
|---|---|---|---|
| Faithfulness (0–1) | claim-level share, 0–1 | Spearman's rho, or Krippendorff's alpha (interval) | MAE, mean signed difference (bias), share within 0.1 |
| Answer relevancy (0–1) | 1–5 ordinal rating | Spearman's rho, or weighted kappa on 5 bins | MAE after mapping, per-bin counts |
| Citation support (yes/no per citation) | yes/no | Cohen's kappa (or Scott's pi) | percent agreement, confusion matrix |
| Decline / not-found correctness | yes/no | percent agreement with the count of disagreements | kappa only if both outcomes occur often |

Reasons:

- Cohen's kappa on binned continuous scores throws away the order and depends on the bin edges; unweighted kappa treats 0.65 vs 0.7 the same as 0.0 vs 1.0. If one statistic must cover faithfulness, quadratic-weighted kappa on fixed bins (for example 0–0.2, ..., 0.8–1.0) is close to an intraclass correlation and keeps order. Krippendorff's alpha with the interval metric does the same without bins and handles more than two raters and missing values (Krippendorff, "Computing Krippendorff's Alpha-Reliability", 2011).
- Correlation alone hides bias: a judge that scores every answer 0.2 higher correlates perfectly. MAE and the mean signed difference catch that, and they matter because we publish absolute faithfulness numbers, not rankings.
- Kappa's prevalence problem: when one outcome dominates (all 6 decline and 5 unanswerable dev questions were handled correctly in Step 12), chance agreement is near 1 and kappa swings widely or is undefined even when the judge never disagrees with the human. Report raw agreement and the disagreement count there.
- For the 3 judge runs: score agreement on the mean of the three, and separately report judge–judge agreement across runs (the same statistic) as the judge's own noise floor.

### 5.4 Sample size

Approximate 95% intervals (normal approximation for kappa, SE ≈ sqrt(p_o(1−p_o)/n)/(1−p_e); Fisher z for rho), computed 2026-10-16:

| Case | n = 60 | n = 100 | n = 150 |
|---|---|---|---|
| kappa 0.70 (p_o 0.85, p_e 0.5) | ±0.18 | ±0.14 | ±0.11 |
| kappa 0.50 (p_o 0.85, p_e 0.7, skewed) | ±0.30 | ±0.23 | ±0.19 |
| rho 0.60 | [0.41, 0.74] | [0.46, 0.71] | — |
| rho 0.70 | [0.54, 0.81] | [0.58, 0.79] | — |

At n = 60 a point estimate of 0.7 cannot be told apart from 0.55, so "kappa ≥ 0.6" is best read as a test on the lower bound or reported with its interval, not as a point threshold. Ragas's docs suggest 100–200 items; ARES uses a few hundred. Citation support is judged per citation, so 60 answers yield several hundred citation judgments, which gives citation support a much tighter interval than the per-answer scores (clustered within answers, so bootstrap by answer, not by citation).

### 5.5 A claim-level protocol for human faithfulness

Following FActScore and Ragas's own definition, so the human and the judge measure the same thing:

1. Show the kept sentences a reader sees (the same text Ragas scores, per the Step 5 decision), the question, and the retrieved chunks the answer could cite, with chunk IDs.
2. Split each sentence into atomic claims: one fact each (a number with its unit and period, a named relation, a comparison). Write the claim list before judging any claim. Record the count.
3. Label each claim against the retrieved chunks only, not outside knowledge: supported (a chunk states it or it follows by trivial arithmetic), not supported (absent or contradicted), or not a claim (hedges, the fixed decline and not-found sentences).
4. Faithfulness = supported / (supported + not supported). An answer with no claims is excluded, as Ragas returns no score for it.
5. Write a reason for every "not supported" and note which chunk was checked.
6. Blind the judge's score until all labels are entered; draw the 60+ answers by a fixed seed, stratified by arm and class, from `dev` only, so calibration never touches `test`.
7. Re-score a random 10–15 answers after a few days to estimate the scorer's own consistency (intra-rater agreement); with one human there is no inter-rater figure, and the write-up should say so.
8. Pace check, as for the eval drafts and the extraction review: log time per answer and flag answers judged in a few seconds.

Answer relevancy is harder to score at claim level; a 1–5 rubric with written anchors ("answers the question asked, for the company and period asked" = 5, "on topic but misses the period or company" = 3, "off topic" = 1) is the usual practice. The ratings must come from the user: under the project's rules only the user's judgments count as human labels.

## Recommendations for Step 13

1. Router: build it as a plain dispatch function with a structured-output classifier on `claude-haiku-5-5`. Do not add LangGraph for Step 13. If the user still wants it for the README diagram, `langgraph==1.2.12` can become a direct dependency at no lock cost, and `draw_mermaid()` (text, never `draw_mermaid_png()`) gives the diagram.
2. Classifier prompt: class descriptions plus 1–2 invented examples per class, never eval questions; enum output with a short reason first; question text only. Compare effort `low` and `medium` on `dev` (estimated under $0.05 per pass).
3. Report router accuracy as a confusion matrix with per-class rows, overall accuracy with an interval and kappa, all labelled "against agent-drafted class labels". Report the routed system against each fixed arm and an oracle router.
4. Spans: a `route` CHAIN span with the predicted class and chosen arm, the classifier as an LLM child span, in `retrieve/tracing.py`, the same as the other arms.
5. For Step 13b: if LangGraph is used for agentic arms, always pass `recursion_limit` (the installed default is 10,007, not the documented 1,000), and count tool calls in state, since the limit counts supersteps.
6. Judge calibration: amend the RESEARCH-PLAN wording before scoring. Keep Cohen's kappa for citation support; use Spearman's rho (or Krippendorff's alpha, interval) with MAE and signed bias for faithfulness and answer relevancy; use percent agreement with disagreement counts for decline and not-found. State each threshold against the interval's lower bound.
7. Sample: at least 60 answers, preferably 100, drawn by fixed seed from `dev`, stratified by arm and class, scored by the user with the claim-level protocol in section 5.5 and a pace check. Bootstrap by answer.
