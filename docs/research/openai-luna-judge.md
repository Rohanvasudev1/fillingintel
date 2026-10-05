# OpenAI gpt-5.6-luna as the Step 5 judge

Research note for the proposed judge switch, written 2026-10-05, before the ticket 08 baseline run. Step 5 judges with `claude-opus-5-5` at effort `medium` through `ClaudeJudge` (`eval/judging/claude.py`). The user wants to judge with OpenAI's `gpt-5.6-luna` instead, mainly for cost. Answers stay with `claude-sonnet-5-5`. Sources: OpenAI's developer docs, fetched as Markdown on 2026-10-05 (append `.md` to any `developers.openai.com/api/docs/...` URL); PyPI JSON metadata; the published wheels of openai 3.3.0 (the version `uv.lock` resolves) and 3.24.0 (latest), ragas 0.4.3 and instructor 1.17.0, unpacked in a scratch directory and never installed into the project. Line numbers refer to files inside those wheels or to the downloaded doc pages. Three claims were checked by running code in a throwaway `uv run --no-project` environment, never in the project: import side effects, strict-schema conversion, and the lock change. Anything not confirmed from a primary source is marked "unverified". `openai.com/index/...` pages returned HTTP 403 and were not read.

## Summary and recommendation

- `gpt-5.6-luna` exists. It is a current API model, not deprecated, with one ID that is also its only snapshot. It costs $0.20 input, $0.02 cached input, $0.25 cache writes and $1.20 output per MTok (standard tier). Reasoning effort runs from `none` to `max`, default `medium`. Knowledge cutoff is 2026-02-16 (section 1).
- A newer and cheaper sibling exists: `gpt-6-luna`, at $0.10 / $0.01 / $0.125 / $0.50, with a 2026-05-18 knowledge cutoff. The models overview lists `gpt-6-luna`, not `gpt-5.6-luna`, as the current efficient model. Ask the user which one they meant before writing code (open question 1).
- Judge cost per dev run, recomputed from the recorded Opus judge token counts: Opus about $24.90, `gpt-5.6-luna` about $1.29, `gpt-6-luna` about $0.62. Batch or Flex halves both luna figures. The luna figures assume luna uses as many tokens as Opus did. Its reasoning-token volume is unknown until a live call, so treat them as lower bounds (section 1.4).
- Do not use Ragas's own OpenAI route. `llm_factory("gpt-5.6-luna", provider="openai")` misclassifies the model as non-reasoning, because its version check runs `int("5.6")`. It would then send `temperature=0.01`, `top_p=0.1` and `max_tokens=1024` to Chat Completions, in instructor's JSON mode, without a strict schema (section 4). OpenAI documents that reasoning models reject `temperature` and `top_p` unless effort is `none`. That rule is documented for the GPT-6 family; for `gpt-5.6-luna` it is unverified.
- The existing pattern carries over. Write an OpenAI judge class that subclasses Ragas's `InstructorBaseRagasLLM`, calls the Responses API with a strict `json_schema` text format and `reasoning.effort`, sets `store=false`, and sends no sampling settings. Every Ragas and project reply schema converts to OpenAI's strict form without errors, and declared key order survives (section 3).
- Use the `openai` SDK, already in `uv.lock` at 3.3.0 through ragas, instructor and langchain-openai. Declaring `openai==3.3.0` as a direct dependency adds two lines to `uv.lock` and changes no versions. `import openai` reads no `.env` file, sends no telemetry and makes no network call (section 5).
- The OpenAI API does not train on API data by default. It keeps abuse-monitoring logs for up to 30 days. The Responses API stores responses for at least 30 days unless `store=false` (section 6).
- OpenAI's eval guidance tells users to "validate agreement against your human labels" before optimizing a judge for cost, and to start with its most capable model. A nano-tier judge with no calibration is the opposite of that advice. Recommendation: before the baseline, run the 10-question luna-vs-Opus spot check (about $3) and keep both judges selectable by config (section 8).

## 1. The model

### 1.1 Identity and limits

- Model ID `gpt-5.6-luna`. "Default snapshot: `gpt-5.6-luna`", and the snapshot list holds only that one ID, so there is no dated snapshot to pin. Source: https://developers.openai.com/api/docs/models/gpt-5.6-luna (lines 7, 15, 97 to 99).
- "GPT-5.6 Luna is designed for cost-sensitive, high-volume workloads. It roughly corresponds to the nano model tier used in earlier GPT-5 families." Same page, lines 9 to 10.
- Context 1,050,000 tokens, maximum input 922,000, maximum output 128,000, "Feb 16, 2026 knowledge cutoff", text and image in, text out, "Reasoning token support". Same page, lines 16 to 22.
- Conflict: the GPT-5.6 migration guide says "Luna has a smaller 400K context and 128K maximum output" (https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol, line 112). The model page says 1.05M. Judge prompts here stay under 15K tokens, so the difference does not matter for this use.
- Endpoints: Chat Completions, Responses and Batch are supported (model page lines 41 to 50).
- Not deprecated. It appears in https://developers.openai.com/api/docs/deprecations only as the recommended replacement for `gpt-5-nano-2025-08-07` (shutdown 2026-12-11) and `gpt-4.1-nano` (shutdown 2026-10-23).
- The GPT-5.6 family has three tiers: `gpt-5.6-sol`, `gpt-5.6-terra` and `gpt-5.6-luna`; the `gpt-5.6` alias routes to Sol. Source: https://developers.openai.com/api/docs/guides/latest-model/gpt-5.6 (line 16).
- Release date: unverified.

### 1.2 Reasoning effort

- "Reasoning.effort supports: none, low, medium (default), high, xhigh, and max." (model page line 11). The GPT-5.6 guide repeats this and adds "If you omit `reasoning.effort`, GPT-5.6 defaults to `medium`" (guides/reasoning line 220).
- Syntax: `reasoning: {"effort": ...}` in Responses, `reasoning_effort` in Chat Completions (upgrading-to-gpt-5p6-sol lines 140 to 158).
- GPT-5.6 also has a `reasoning.mode` of `standard` (default) or `pro` in the Responses API (guides/reasoning line 218). Leave it at the default and record it.
- `project` constraint: `retrieve/answer_model.py` defines `Effort = Literal["low", "medium", "high", "xhigh", "max"]`. `medium` fits; `none` would need the type widened.

### 1.3 Pricing (read 2026-10-05)

From https://developers.openai.com/api/docs/pricing, "Short context" columns (prompts up to 272K input tokens), USD per MTok:

| Model | Tier | Input | Cached input | Cache writes | Output | Source line |
|---|---|---|---|---|---|---|
| gpt-5.6-luna | Standard | 0.20 | 0.02 | 0.25 | 1.20 | 40 |
| gpt-5.6-luna | Batch | 0.10 | 0.01 | 0.125 | 0.60 | 94 |
| gpt-5.6-luna | Flex | 0.10 | 0.01 | 0.125 | 0.60 | 146 |
| gpt-6-luna | Standard | 0.10 | 0.01 | 0.125 | 0.50 | 36 |
| gpt-6-luna | Batch | 0.05 | 0.005 | 0.0625 | 0.25 | 90 |
| gpt-6-luna | Flex | 0.05 | 0.005 | 0.0625 | 0.25 | 142 |

- "Prompts with >272K input tokens are priced at 2x input and 1.5x output for the full request." "Cache writes are billed at 1.25x the uncached input token rate." Model page lines 36 to 37.
- Reasoning tokens "are billed as output tokens" (guides/reasoning line 250).
- Prompt caching is implicit by default on GPT-5.6, with a 1,024-token minimum prefix (guides/prompt-caching lines 69 and 97). The three judge runs send identical prompts, so runs 2 and 3 may read from the cache at $0.02. Run 1 may pay the 1.25x write rate on the cached prefix. `prompt_cache_options.mode: "explicit"` with no breakpoints should avoid writes (line 648); unverified.

### 1.4 Judge cost per dev run

The repo does not record the "~$26" Opus figure or the "~$1.30" luna figure directly. They can be reconstructed from the live judge capture of ticket 07, whose responses are committed in `tests/fixtures/judge/responses/claude-opus-5-5/medium/` (8 files, 22,216 input and 1,293 output tokens):

- One judge run on an answerable question (q0072): 6 calls (statement extraction, faithfulness verdicts, 3 relevancy prompts, citation support). Together: 20,675 input and 874 output tokens. Opus output tokens include its thinking.
- One judge run on a decline or not-found question: 1 call, about 770 input and 210 output tokens (the mean of the q0005 and q0011 captures).
- Dev split (`eval/agent_drafted_set.jsonl`): 82 answerable (27 lookup, 22 local, 23 multi_hop, 10 global) and 11 decline or unanswerable. Each judge runs 3 times.

| Judge | Per answerable run | Per decline run | Dev run (82 × 3 + 11 × 3) |
|---|---|---|---|
| claude-opus-5-5 ($4 / $20) | $0.1002 | $0.0073 | **$24.89** |
| gpt-5.6-luna standard ($0.20 / $1.20) | $0.00518 | $0.00041 | **$1.29** |
| gpt-5.6-luna, Batch or Flex | $0.00259 | $0.00020 | $0.64 |
| gpt-6-luna standard ($0.10 / $0.50) | $0.00250 | $0.00018 | $0.62 |

Caveats:
- The luna rows reuse Opus's token counts. OpenAI's tokenizer counts the same text differently (unverified by how much), and luna's reasoning-token volume at `medium` is unknown. If luna spends five times Opus's output tokens, the standard `gpt-5.6-luna` run costs about $2.31. Either way it stays below $3.
- If every input token were billed as a cache write, the standard `gpt-5.6-luna` run would rise to about $1.52.
- The ticket 07 BUILD-LOG entry says "about $0.10 per answerable question across three runs ... roughly $10 to $15 for the dev split". The fixtures show $0.10 per run, so about $0.30 per question across three runs and about $25 for dev. That entry appears to understate the Opus cost by about half. Flagged, not edited.

## 2. Request parameters

| Parameter | Status for gpt-5.6-luna | Source |
|---|---|---|
| `reasoning.effort` | `none` to `max`, default `medium` | model page line 11 |
| `temperature`, `top_p`, `top_logprobs` | For GPT-6 models: "When reasoning effort is not `none`, remove `temperature`, `top_p`, and `top_logprobs`." For GPT-5.6 Luna: no explicit statement found; unverified. Send neither. | guides/latest-model (GPT-6) line 205 |
| `seed` | Chat Completions only, "in Beta ... best effort ... Determinism is not guaranteed". The Responses API has no `seed` parameter. | openai 3.3.0 `resources/chat/completions/completions.py` lines 479 to 483; `seed` absent from `resources/responses/responses.py` and `types/responses/response_create_params.py`; guides/advanced-usage line 9 |
| `text.verbosity` | `low`, `medium`, `high`; documented for GPT-5.6 | guides/latest-model/gpt-5.6 lines 116 to 120; reference `responses/methods/create` line 5020 |
| `max_output_tokens` | "An upper bound ... including visible output tokens and reasoning tokens" | reference `responses/methods/create` line 4425 |
| `store` | Responses: "Defaults to true when omitted"; stored for at least 30 days | reference line 4906 |

Notes:
- Determinism: there is no seed on the Responses API, and the Chat Completions seed is best effort. As with Claude, repeatability comes from the response cache, not from sampling settings. The research plan's three judge runs and the uncached repeat run measure the remaining noise.
- Hitting `max_output_tokens` returns `status: "incomplete"` with `incomplete_details.reason: "max_output_tokens"`, possibly "before any visible output tokens are produced", after the input and reasoning tokens are already billed. OpenAI recommends "reserving at least 25,000 tokens for reasoning and outputs when you start experimenting" (guides/reasoning lines 285 to 287). The Opus judge uses 16,000; use 25,000 for luna, then lower it once real reasoning-token counts are known.
- In openai 3.3.0, `responses.parse()` takes a top-level `verbosity=` keyword and puts it in the body as a top-level key (`resources/responses/responses.py` lines 1337 and 1398). The API reference and `ResponseTextConfigParam` (`types/responses/response_text_config_param.py` line 38) place verbosity under `text`. Pass it as `text={"verbosity": ...}` or leave it out. How the API treats the top-level key is unverified.
- GPT-5.6 output passes through real-time cyber and biology classifiers that "may occasionally intervene on legitimate work" (guides/latest-model/gpt-5.6, Safeguards). SEC filing text is unlikely to trigger them; count any refusal as a judge error, as now.

## 3. Structured outputs

### Responses API or Chat Completions

- OpenAI's GPT-5.6 guidance: "Use the Responses API for reasoning, tool-calling, and multi-turn workflows" (guides/latest-model/gpt-5.6 line 60). Both endpoints support structured outputs for luna. Choose Responses, with `store=false` set explicitly (section 6).
- Responses: `text={"format": {"type": "json_schema", "name": ..., "strict": True, "schema": ...}}`. `client.responses.parse(text_format=Model)` builds this from a Pydantic model: it sets `text["format"] = _type_to_text_format_param(text_format)` and raises `TypeError` if `text.format` is also given (openai 3.3.0 `resources/responses/responses.py` lines 1345 to 1352). The helper always sets `"strict": True` (`lib/_parsing/_responses.py` lines 38 to 50).
- Chat Completions: `client.chat.completions.parse(response_format=Model)` (`resources/chat/completions/completions.py` line 91), which also accepts `reasoning_effort`, `seed`, `verbosity` and `store`.
- Both helpers exist unchanged in 3.24.0. The only difference in `responses.parse`'s signature is a new `access_programs` argument.
- Following the precedent of ticket 07, which used `messages.create` rather than `messages.parse`, prefer `responses.create` with the format built once. It returns the raw body that `retrieve/response_cache.py` stores, and parsing stays in project code. To build the schema, use the SDK's `openai.lib._pydantic.to_strict_json_schema` (a private module), or `type_to_response_format_param` from `openai.lib._parsing` (also private). Using private modules is a tradeoff; a project copy of the strict transform is the other option.

### Strict-schema rules

From https://developers.openai.com/api/docs/guides/structured-outputs ("Supported schemas", lines 3576 to 3973):
- Root must be an object, not `anyOf`. Every field must be `required`. `additionalProperties: false` must be set on every object. Up to 5,000 properties and 10 levels of nesting.
- Unsupported: `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if`, `then`, `else`. "If you turn on Structured Outputs by supplying `strict: true` and call the API with an unsupported JSON Schema, you will receive an error."
- "outputs will be produced in the same order as the ordering of keys in the schema" (line 3847). This matters here: ticket 07 found that sorting schema keys put the verdict ahead of its reason and emptied the reasons. Keep declared order, as `output_schema()` does now.
- The SDK's `_ensure_strict_json_schema` sets `additionalProperties: false`, marks every property required, inlines `$ref`s that have siblings, and strips `None` defaults (openai 3.3.0 `lib/_pydantic.py` lines 27 to 115).
- Checked by running `to_strict_json_schema` (openai 3.3.0) on copies of the four reply models: Ragas's `StatementGeneratorOutput`, `NLIStatementOutput` and `AnswerRelevanceOutput`, and the project's `CitationSupportOutput`. All four converted without error, with declared field order kept (`reason` before `verdict`, `reason` before `supported`). `BehaviourVerdict` uses the same field types. Whether the API accepts them is unverified until a live call.

### Refusals, truncation and usage

- `ParsedResponse.output_parsed` returns the first `output_text` item's parsed value, or `None` (openai 3.3.0 `types/responses/parsed_response.py` lines 117 to 124). A refusal arrives as a content item of type `refusal`, which `parse_response` passes through unparsed (`lib/_parsing/_responses.py` lines 62 to 66). The guide lists refusals and token-limit truncation as the two cases where output does not match the schema (structured-outputs line 2448).
- The judge should raise `JudgeReplyError`, as `ClaudeJudge` does, when `status != "completed"` (with `incomplete_details.reason`), when any content item is a refusal, or when `output_text` fails `model_validate_json`.
- Usage is on `response.usage` (`types/responses/response.py` line 465): `input_tokens`, `input_tokens_details.cached_tokens`, `input_tokens_details.cache_write_tokens`, `output_tokens`, `output_tokens_details.reasoning_tokens`, `total_tokens` (`types/responses/response_usage.py`). Unlike Anthropic, `input_tokens` includes the cached and written tokens. OpenAI's own cost example computes `ordinary_input_tokens = input_tokens - cached_tokens - cache_write_tokens` (guides/prompt-caching lines 594 to 597). `retrieve/pricing.anthropic_cost` cannot be reused; luna needs an `openai_cost` with that subtraction. Reasoning tokens are already inside `output_tokens`.
- Chat Completions usage: `completion_tokens_details.reasoning_tokens`, `prompt_tokens_details.cached_tokens` and `cache_write_tokens` (`types/completion_usage.py` lines 22, 43, 46).
- Record `response.model` with every result, as the migration guide advises for aliases (upgrading-to-gpt-5p6-sol line 119).

## 4. Ragas 0.4.3 with OpenAI

### What the documented route does

- `llm_factory(model, provider="openai", client=OpenAI())`. `"openai"` is the default provider (`ragas/llms/base.py` line 581). For OpenAI, Ragas wraps the client with `instructor.from_openai(client, mode=instructor.Mode.JSON)` (line 556), to avoid a function-calling issue with `Dict` fields (lines 541 to 546).
- instructor's OpenAI JSON mode sends `response_format={"type": "json_object"}` and prepends a system message containing the schema text: "As a genius expert, your task is to understand the content and provide the parsed objects in json ..." (`instructor/v2/providers/openai/handlers.py` lines 850 to 893). This is JSON mode, not strict structured output, so the schema is not enforced. instructor re-asks on validation failure, up to `max_retries=3` by default (`instructor/v2/core/client.py` line 65), and those extra calls are invisible to Ragas.
- `InstructorModelArgs` defaults are `temperature=0.01`, `top_p=0.1`, `max_tokens=1024` (`ragas/llms/base.py` lines 724 to 726). For OpenAI, `_map_openai_params` (lines 805 to 883) detects reasoning models. For those it renames `max_tokens` to `max_completion_tokens`, sets `temperature=1.0` and drops `top_p`.
- The detection fails for `gpt-5.6-luna`. It takes `model[4:].split("-")[0]`, which is `"5.6"`, and calls `int()` on it inside `try/except ValueError` (lines 853 to 860). `int("5.6")` raises, so the model counts as non-reasoning. Ragas then sends `temperature=0.01`, `top_p=0.1` and `max_tokens=1024` to Chat Completions. Checked by running the same expression: `gpt-5.6-luna` gives a ValueError, while `gpt-6-luna` and `gpt-5-nano` are detected.
- For `gpt-5.6-luna` at effort `medium`, OpenAI's documented rule for reasoning models (stated for GPT-6) would reject `temperature` and `top_p`. Whether 5.6 rejects them is unverified. Even if it accepted them, `max_tokens=1024` would truncate reasoning plus output, and the request would carry no reasoning effort. For `gpt-6-luna` the route would send `temperature=1.0` and `max_completion_tokens=1024`. That is still too small, still not strict, and still sets no effort.
- Ragas returns only the parsed model, so token usage is lost on this route, as noted for Anthropic in `docs/research/ragas-deepeval-claude-judge.md` section 4.

Conclusion: the documented route fails the same way it did for Opus, plus the version-parsing bug. Keep the custom judge class.

### The existing pattern carries over

The collections metrics accept any `InstructorBaseRagasLLM` subclass and only call `agenerate(prompt, response_model)` (earlier note, section 2). Nothing in that contract is Anthropic-specific. The harness already has the seam: `JudgeSpec(required_env, open)` in `eval/judging/runner.py`, passed to `eval.run` as `judges=CLAUDE_JUDGES`.

Sketch (not run; shapes only):

```python
# eval/judging/openai_judge.py
JUDGE_MODEL = "gpt-5.6-luna"          # or gpt-6-luna; open question 1
JUDGE_EFFORT: Effort = "medium"
JUDGE_MAX_OUTPUT_TOKENS = 25_000      # reasoning plus JSON; OpenAI's starting reserve


@dataclass(frozen=True)
class OpenAIJudgeRequest:              # satisfies the ModelRequest protocol
    model: str
    effort: Effort
    max_output_tokens: int
    prompt: str
    schema: str                        # to_strict_json_schema(...), declared key order
    schema_name: str
    run: int
    repeat: int

    def params(self) -> dict[str, object]:
        return {
            "model": self.model,
            "input": [{"role": "user", "content": self.prompt}],
            "reasoning": {"effort": self.effort},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "text": {"format": {"type": "json_schema", "name": self.schema_name,
                                "strict": True, "schema": json.loads(self.schema)}},
        }

    def cache_key(self) -> str:        # add "provider": "openai" to the hashed dict
        ...


class OpenAIResponsesModel:            # the backend; same shape as AnthropicAnswerModel
    @classmethod
    def from_env(cls): ...             # reads OPENAI_API_KEY, fails with a clear message
    def complete(self, request) -> ApiResponse:
        response = self._client.responses.create(**request.params())
        return ApiResponse(body=response.model_dump(mode="json"), api_ms=..., from_cache=False)


class OpenAIJudge(InstructorBaseRagasLLM):
    # same run/repeat counters and calls tuple as ClaudeJudge
    def generate(self, prompt, response_model):
        body = self._backend.complete(self._request(prompt, response_model)).body
        # record usage and openai_cost(model, usage) first: tokens are spent either way
        # status != "completed"           -> JudgeReplyError(incomplete_details.reason)
        # any content item type "refusal" -> JudgeReplyError("refusal")
        # model_validate_json(output_text) fails -> JudgeReplyError
        ...

    async def agenerate(self, prompt, response_model):
        return self.generate(prompt, response_model)
```

Shared pieces: `CachedAnswerModel` stores any `ModelRequest` under `{model}/{effort}/{key}.json`, so OpenAI responses land in their own model directory. `parse_reply`, `TokenUsage` and `anthropic_cost` are Anthropic-shaped and need OpenAI counterparts. `JudgeCall.usage` and the report's token fields need a provider-neutral shape (input, cached, cache-write, output, reasoning). The answer relevancy embedder stays on Voyage and does not change.

## 5. SDK side effects and dependency

### Import and client construction

- No `.env` loading: the openai 3.3.0 package contains no `dotenv` reference (grep of the wheel). Checked by running: in a scratch directory holding a `.env` with a dummy `OPENAI_API_KEY`, `import openai` left `OPENAI_API_KEY` unset in `os.environ`.
- No telemetry: no `telemetry`, `posthog` or `sentry` reference in the wheel. Each request carries `X-Stainless-*` platform headers (language, package version, OS; `_base_client.py` lines 2207 to 2211), which is request metadata, not a separate call.
- No network at import or at client construction. Checked by running `import openai; openai.OpenAI(api_key=...)` with `socket.connect`, `create_connection` and `getaddrinfo` patched to raise; nothing raised.
- At import, `openai/__init__.py` reads `OPENAI_API_TYPE`, `OPENAI_API_VERSION`, `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_AD_TOKEN` into module globals (lines 163 to 169) and calls `setup_logging()` (line 116), which reads `OPENAI_LOG` (`_utils/_logs.py` line 23). `OPENAI_LOG=debug` logs requests at DEBUG; a `SensitiveHeadersFilter` redacts auth headers (`_logs.py`).
- `OpenAI()` reads `OPENAI_API_KEY`, `OPENAI_ADMIN_KEY`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, `OPENAI_WEBHOOK_SECRET`, `OPENAI_BASE_URL` and `OPENAI_CUSTOM_HEADERS` (`_client.py` lines 242 to 302). With no credential it raises `OpenAIError("Missing credentials ...")` (lines 258 to 266). Defaults: timeout 600 s (connect 5 s), `max_retries=2` (`_constants.py` lines 7 to 8).
- `OPENAI_BASE_URL` redirects every request, key included, to another host. Pass `base_url="https://api.openai.com/v1"` explicitly in `from_env`, or refuse to start when the variable is set, so a stray shell variable cannot send the key elsewhere (invariant 7).
- openai 3.x is built on `httpx2`, like anthropic 1.11.0 (`uv.lock`: openai 3.3.0 and anthropic 1.11.0 both depend on `httpx2`, locked at 2.13.1).

### Dependency change

- `uv.lock` already resolves `openai==3.3.0` (uploaded 2026-08-18), pulled in by ragas, instructor and langchain-openai. Latest is 3.24.0 (2026-10-02, `Requires-Python >=3.10`). Source: https://pypi.org/pypi/openai/json.
- Checked on a scratch copy of `pyproject.toml` and `uv.lock` (`uv add --no-sync`, not in the project): `uv add openai==3.3.0` adds two lines to `uv.lock`, the dependency entry and the `==3.3.0` specifier, and changes no versions. `uv add openai` without a pin writes `openai>=3.3.0` and also keeps 3.3.0.
- The CLAUDE.md rule "Ask before adding a dependency" applies to declaring it, even though nothing new is installed. Pin `==3.3.0`, as with `anthropic==1.11.0`. 3.3.0 already lists `gpt-5.6-luna` in `ChatModel` (`types/shared/chat_model.py` line 10); `gpt-6-luna` first appears in a later version (present in 3.24.0). Model IDs are plain strings, so either works with 3.3.0. That a 3.3.0 `responses.create` call to `gpt-6-luna` succeeds is unverified.

### SDK or httpx

| | openai SDK 3.3.0 | httpx, like `ingest/voyage.py` |
|---|---|---|
| New packages | None (already locked); declare it | None |
| Strict schema | `to_strict_json_schema` (private module) | Write and test our own transform |
| Retries | Built in (`max_retries`, backoff on 429/5xx) | Write them, as for Voyage |
| Response typing | Typed `Response`, `model_dump()` for the cache | Plain dict; validate it ourselves |
| Surface | Large client, many env vars (above) | Only what we send |
| Consistency | Matches how the Anthropic side uses its SDK | Matches the Voyage client |

Recommendation: use the SDK. The judge already depends on the Anthropic SDK's `transform_schema`, and the OpenAI strict transform has the rules listed in section 3. Reimplementing it is where bugs would come from. Use httpx only if the user wants to avoid relying on a private SDK module.

## 6. Data handling

- "As of March 1, 2023, data sent to the OpenAI API is not used to train or improve OpenAI models (unless you explicitly opt in to share data with us)." Source: https://developers.openai.com/api/docs/guides/your-data, line 7.
- Abuse-monitoring logs, which may hold prompts and responses, are "retained for up to 30 days" by default (line 18). Zero Data Retention and Modified Abuse Monitoring need OpenAI's prior approval (line 20).
- `/v1/responses` and `/v1/chat/completions`: "Data used for training: No", abuse monitoring 30 days (table, lines 72 to 73).
- Responses API: "a 30 day Application State retention period by default, or when the `store` parameter is set to `true`" (line 108). The reference says `store` "Defaults to true when omitted" (reference `responses/methods/create` line 4906). Send `store=false`. Under ZDR, `store` is always treated as false (line 109).
- Chat Completions `store` controls storage "for use in our model distillation or evals products" (openai 3.3.0 `completions.py` lines 511 to 513). Its default is unverified here.
- Prompt caching may hold encrypted key/value tensors on GPU-local storage for up to 24 hours (your-data line 104).
- What is sent: the question, the answer and retrieved chunk text from public SEC filings. No personal data and no secrets. Record this in the ADR.

## 7. Rate limits, Batch and Flex

- `gpt-5.6-luna` limits (model page lines 107 to 113):

| Tier | RPM | TPM | Batch queue |
|---|---|---|---|
| 1 | 500 | 500,000 | 5,000,000 |
| 2 | 5,000 | 2,000,000 | 20,000,000 |
| 3 | 5,000 | 4,000,000 | 40,000,000 |
| 4 | 10,000 | 10,000,000 | 1,000,000,000 |
| 5 | 30,000 | 180,000,000 | 15,000,000,000 |

- Tier 1 needs "$5 paid" (guides/rate-limits lines 40 to 48). The account's tier is unverified; check it at platform.openai.com/settings/organization/limits.
- Load estimate: a dev run is about 1,509 judge calls (82 × 6 × 3 + 11 × 3) and about 5.1M input tokens. With 8 worker threads and requests taking a few seconds, Tier 1's 500,000 TPM allows the whole run in roughly 10 to 15 minutes. The SDK retries 429s twice by default; raise `max_retries` as `retrieve/answer_model.py` does (6).
- Batch API: "50% cost discount", "a separate pool of significantly higher rate limits", completion "within 24 hours", `/v1/responses` supported, up to 50,000 requests and 200 MB per batch (guides/batch lines 5 to 29 and 676). luna supports Batch (model page line 50). It does not fit the current design: Ragas calls `agenerate` one prompt at a time and needs each reply before the next step (statements, then verdicts), so Batch would require a two-phase pipeline outside Ragas.
- Flex processing: `service_tier="flex"` on a normal request, priced at Batch rates, "ideal for ... model evaluations", slower, with occasional `429 Resource Unavailable` errors that are not charged (guides/flex-processing lines 5 to 13 and 169 to 171). luna is on the Flex price list (pricing line 146). Flex is the cheap option that fits the synchronous judge. It saves about $0.65 per run, so it is optional; it adds timeouts and retries to handle.

## 8. Benchmark validity

From OpenAI's sources:
- "Use the most capable model to grade if you can. Start with `gpt-6-astra` ... then validate agreement against your human labels before optimizing for cost or latency." (https://developers.openai.com/api/docs/guides/evaluation-best-practices, lines 424 and 433). A nano-tier judge is cost optimization of exactly the kind this advice says to validate first.
- Known judge biases listed: "Position bias (response order), verbosity bias (preferring longer responses)". Recommended: pass/fail or pairwise grading, "reasoning and chain-of-thought ... before scoring", clear rubrics (lines 430 to 438). The Ragas verdicts and project judges are already binary with a reason field written first, which matches.
- "Maintain agreement: Use human feedback to calibrate automated scoring." (line 42).

The project's own reasoning (not a sourced claim):
- With Sonnet 5.5 answering and Opus 5.5 judging, both models come from one vendor and model family, so same-family self-preference is a known risk for LLM judges. A cross-vendor judge removes that particular bias. That is a validity argument for luna, separate from cost.
- Against it: a nano-tier model may be a weaker judge of numeric claims in financial tables than Opus. Faithfulness verdicts on 10-K tables are the hardest part of the job. Every judged number is already labelled "uncalibrated", but a weaker judge widens the gap that calibration must close.
- Switching the judge changes what the baseline measures. Every later arm must use the same judge, model ID and effort, or the comparison breaks. Whichever judge the baseline uses becomes the judge for the rest of the project, unless all arms are rescored.
- A 10-question spot check, scoring the same answers with both judges on the same prompts, gives a first agreement figure for about $3 (mostly Opus). Ten questions show only gross disagreement; it does not replace calibration.

## Open questions for /grill-with-docs

1. **Which model?** `gpt-5.6-luna` as asked, or `gpt-6-luna`, which is newer, half the price and has a later cutoff? Recommendation: `gpt-6-luna`, if the user's goal is the cheapest current efficient model; otherwise `gpt-5.6-luna` as asked. Either way, record the exact ID, since neither has a dated snapshot.
2. **Swap, or keep both behind config?** Recommendation: keep both. `JudgeSpec` is already the seam; add `OPENAI_JUDGES` beside `CLAUDE_JUDGES` and choose with a `--judge` flag or a config constant, recorded in the results header. That also allows the spot check and a later Opus rescore of the baseline.
3. **Spot check first?** Recommendation: yes. Judge 10 dev questions with both judges before the baseline and report per-metric agreement. Decide on the result, and write down the agreement threshold before looking.
4. **SDK or httpx?** Recommendation: SDK, pinned at `openai==3.3.0` (already locked; needs the user's approval as a declared dependency).
5. **Responses or Chat Completions?** Recommendation: Responses with `store=false`, strict `json_schema`, `reasoning.effort` and no sampling settings.
6. **Effort?** Recommendation: `medium`, the default and the current Opus setting. Ask whether `high` is worth testing in the spot check, given luna's lower capability.
7. **Cache key.** Recommendation: add `"provider": "openai"` to the hashed key, and keep the `{model}/{effort}` directory layout. Changing the key format for Anthropic requests would invalidate the recorded fixtures, so leave the Anthropic key as it is.
8. **Startup key check.** Recommendation: `eval.run` checks `OPENAI_API_KEY` when the OpenAI judge is selected, and `ANTHROPIC_API_KEY` always (answers). `from_env` sets `base_url` explicitly or refuses to start when `OPENAI_BASE_URL` is set. `.env.example` gains an empty `OPENAI_API_KEY`.
9. **Pricing and usage.** Recommendation: add an `openai_cost` that subtracts cached and cache-write tokens from `input_tokens`, add luna rows to the dated price table with the OpenAI source URL, and record reasoning tokens separately in the report.
10. **max_output_tokens.** Recommendation: 25,000 to start (OpenAI's guidance), then lower it once the spot check shows real reasoning-token counts. Changing it later invalidates cached judge responses.
11. **Flex?** Recommendation: not for the baseline. The saving is under $1 per run and Flex adds timeouts and 429 handling. Revisit if judge volume grows.
12. **The ticket 07 cost note.** It says about $10 to $15 for the dev split; the fixtures imply about $25. Correct it in the next BUILD-LOG entry.
