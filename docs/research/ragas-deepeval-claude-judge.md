# Ragas and DeepEval with Claude as the judge

Research note for ticket 01 (`.scratch/step-5/issues/01-research-claude-judges.md`), written 2026-10-05 for ticket 07. Sources are Anthropic's docs, PyPI metadata, and the published wheels of ragas 0.4.3, deepeval 4.2.8 and instructor 1.17.0, unpacked in a scratch directory and read without installing them into the project. Line numbers refer to files inside those wheels. Anything not confirmed from a primary source is marked "unverified".

## Summary and recommendation

- Pin `ragas==0.4.3`, `deepeval==4.2.8` (only if kept) and `anthropic==1.11.0`. The three resolve together on Python 3.11 (`uv pip compile`, 116 packages).
- Ragas cannot use `claude-opus-5-5` through its documented route, `llm_factory("...", provider="anthropic", client=Anthropic())`. That route sends `temperature=0.01` and `top_p=0.1` and, through instructor's default tools mode, a forced `tool_choice`. Claude Opus 5.5 rejects all three with a 400. Ticket 07 should write a small judge class that subclasses Ragas's `InstructorBaseRagasLLM` and calls `client.messages.parse(output_format=Model)` directly. The collections metrics (`ragas.metrics.collections.Faithfulness`, `AnswerRelevancy`) accept any subclass, and the class sees each response's `usage`, so it also does the token accounting Ragas lacks on this path.
- Answer relevancy also needs an embedder. Wrap the Step 5 embedding model in a `BaseRagasEmbedding` subclass rather than adding a LangChain or Hugging Face package.
- DeepEval's `AnthropicModel` already knows `claude-opus-5-5`: it sends no temperature and no thinking block, uses `output_config.format`, and prices calls at $4/$20 per MTok. It works without an adapter.
- Recommendation: drop DeepEval from the benchmark. Its default faithfulness passes claims the context does not support, as long as nothing contradicts them, so it measures something weaker than Ragas, not a second opinion on the same thing. Installing it also changes every project pytest run: it auto-registers a pytest plugin that imports deepeval, which loads `.env` from the working directory unless `DEEPEVAL_DISABLE_DOTENV=1` is set, and it pulls in pytest-asyncio, pytest-xdist, pytest-repeat and pytest-rerunfailures. DeepEval is in the CLAUDE.md stack, so dropping it is the user's decision. If the user keeps it, run it with `penalize_ambiguous_claims=True` and report it separately (section 7).
- No package is needed beyond anthropic, ragas and deepeval. Ragas pulls in a large transitive set (LangChain, OpenAI SDK, instructor, datasets); see section 1.
- Set `RAGAS_DO_NOT_TRACK=true`, `DEEPEVAL_TELEMETRY_OPT_OUT=1` and `DEEPEVAL_DISABLE_DOTENV=1` in the environment and in CI.

## 1. Versions and dependencies

- Latest ragas is 0.4.3, uploaded 2026-01-13, `Requires-Python >=3.9`. No newer release exists as of 2026-10-05. Source: https://pypi.org/pypi/ragas/json.
- Latest deepeval is 4.2.8, uploaded 2026-10-02, `Requires-Python <4.0,>=3.9`. It ships a release every few days (4.2.1 to 4.2.8 in September and October 2026), so pin it exactly. Source: https://pypi.org/pypi/deepeval/json.
- Latest anthropic is 1.11.0, `Requires-Python >=3.10`, built on `httpx2`. Source: https://pypi.org/pypi/anthropic/1.11.0/json.
- ragas 0.4.3 has hard dependencies on `openai>=1.0.0`, `instructor`, `langchain`, `langchain-core`, `langchain-community`, `langchain_openai`, `tiktoken`, `datasets>=4.0.0`, `diskcache`, `networkx`, `scikit-network`, `nest-asyncio` and `appdirs`. `uv add ragas` therefore installs LangChain and the OpenAI SDK even if neither is used. Source: `requires_dist` at https://pypi.org/pypi/ragas/0.4.3/json.
- deepeval 4.2.8 depends on `openai`, `posthog>=7,<8` (Python 3.10+), `opentelemetry-api`, `opentelemetry-sdk`, `grpcio`, `requests`, `python-dotenv`, `pytest`, `pytest-asyncio`, `pytest-xdist`, `pytest-repeat` and `pytest-rerunfailures`. It depends on neither LangChain nor `anthropic`; `AnthropicModel` imports `anthropic` on demand (`require_dependency`, `models/llms/anthropic_model.py` line 305). Source: `requires_dist` at https://pypi.org/pypi/deepeval/4.2.8/json.
- instructor 1.17.0's `anthropic` extra pins `anthropic==0.93.0`, which conflicts with anthropic 1.x. Ragas depends on plain `instructor` without the extra, so there is no conflict unless someone adds `instructor[anthropic]`. Source: https://pypi.org/pypi/instructor/1.17.0/json.
- A dry resolution of `ragas==0.4.3`, `deepeval==4.2.8` and `anthropic==1.11.0` for Python 3.11 succeeds with 116 packages, including `instructor==1.17.0`, `openai==3.3.0`, `langchain==1.4.3`, `datasets==5.0.1`, `posthog==7.62.1` and `pytest==9.1.1`. Source: `uv pip compile` run in the scratch directory on 2026-10-05, not in the project.

## 2. Ragas with Claude as the judge

### What the documented route does

- `llm_factory(model, provider="anthropic", client=...)` routes to the instructor adapter by default (`ragas/llms/base.py` lines 579 to 714, `ragas/llms/adapters/__init__.py` `auto_detect_adapter`). The docstring example is `llm_factory("claude-3-sonnet", provider="anthropic", client=Anthropic(...))`.
- The adapter always builds `InstructorModelArgs()`, whose defaults are `temperature=0.01`, `top_p=0.1` and `max_tokens=1024` (`ragas/llms/base.py` lines 717 to 727, `ragas/llms/adapters/instructor.py`). For Anthropic these pass through unchanged (`_map_provider_params`, line 786 onward: "Anthropic: No special handling required"). Keyword arguments to `llm_factory` can override the values but cannot remove the keys.
- Ragas wraps the client with `instructor.from_anthropic(client)` and no mode argument (`ragas/llms/base.py` line 558). instructor's default is `Mode.TOOLS` (`instructor/v2/providers/anthropic/client.py` line 33). In that mode, unless thinking is `enabled`, instructor sets `tool_choice={"type": "tool", ...}` (`instructor/v2/providers/anthropic/handlers.py` lines 383 to 409).
- Claude Opus 5.5 rejects each of these. The migration guide says: "Omit `temperature`, `top_p`, and `top_k`, or leave them at their defaults: any other value is rejected", and forced tool choice with `any` or `tool` "is rejected". It adds that the Python SDK v1.0 and later does not define the sampling parameters, so passing them raises a `TypeError`. Source: https://platform.claude.com/docs/en/models/opus-5-5/migration-guide (sections "What every request to Claude Opus 5.5 must satisfy" and "Sampling parameters removed").
- instructor's `Mode.JSON_SCHEMA` sends the deprecated `output_format` with the `structured-outputs-2025-11-13` beta (`handlers.py` lines 771 to 850). The same guide says the Python SDK v1.0 and later "does not accept `output_format={...}` on `client.beta.messages.create()`". That mode is not a way out either.
- The `max_tokens=1024` default would also be too small: on Claude Opus 5.5 `max_tokens` "remains a hard limit on total output, thinking plus response text", and thinking cannot be disabled. Source: migration guide, same page.
- The legacy path (`ragas.metrics.Faithfulness` with `LangchainLLMWrapper(ChatAnthropic(...))`) needs `langchain-anthropic`, an extra package, and `LangchainLLMWrapper.generate_text` sets `langchain_llm.temperature` before every call (`ragas/llms/base.py` lines 231 to 263). The `bypass_temperature` flag is checked only in the async path (line 291). Not recommended.

### Supported alternative: a custom judge class

- The collections metrics take any `InstructorBaseRagasLLM`. `BaseMetric` validates with `isinstance(llm, InstructorBaseRagasLLM)` (`ragas/metrics/collections/base.py` line 117), and the metrics only call `await self.llm.agenerate(prompt_str, ResponseModel)` (`ragas/metrics/collections/faithfulness/metric.py` lines 152 to 166). The abstract methods are `generate(prompt, response_model)` and `agenerate(prompt, response_model)` (`ragas/llms/base.py` lines 730 to 748).
- The Anthropic Python SDK's `client.messages.parse(..., output_format=PydanticModel)` returns a validated instance in `response.parsed_output`. Source: https://platform.claude.com/docs/en/build-with-claude/structured-outputs. The migration guide confirms "the `output_format=Model` argument of the `parse()` and `stream()` helpers is unchanged" in SDK v1.0 and later.
- Pydantic constraints such as `minimum` or `maxLength` are not supported by the API schema; SDK helpers move them into descriptions. Source: structured outputs page above. The Ragas response models use plain `str`, `int` and `List` fields (`faithfulness/util.py`, `answer_relevancy/util.py`), so this should not bite. Unverified until a live call.
- Claude Opus 5.5 defaults to `medium` effort, set with `output_config={"effort": ...}`. Source: https://platform.claude.com/docs/en/about-claude/models/overview ("Default effort"). Whether `messages.parse` merges a caller's `output_config={"effort": ...}` with the `format` it builds from `output_format` is unverified; ticket 07 should check it with one live call and fall back to `messages.create(output_config={"effort": ..., "format": {"type": "json_schema", "schema": ...}})` if not.

Sketch (not run):

```python
import anthropic
from pydantic import BaseModel
from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections import AnswerRelevancy, Faithfulness

JUDGE_MODEL = "claude-opus-5-5"


class ClaudeJudge(InstructorBaseRagasLLM):
    def __init__(self, model: str = JUDGE_MODEL, effort: str = "medium", max_tokens: int = 16000):
        self._client = anthropic.Anthropic()        # reads ANTHROPIC_API_KEY from the environment
        self._aclient = anthropic.AsyncAnthropic()
        self._model, self._effort, self._max_tokens = model, effort, max_tokens
        self.calls: tuple = ()                      # (input_tokens, output_tokens) per call

    def _kwargs(self, prompt: str, response_model: type[BaseModel]) -> dict:
        return dict(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
            output_format=response_model,
            output_config={"effort": self._effort},  # merge with format: unverified
        )

    def _unwrap(self, response):
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"judge stopped with {response.stop_reason}")
        self.calls = (*self.calls, (response.usage.input_tokens, response.usage.output_tokens))
        return response.parsed_output

    def generate(self, prompt, response_model):
        return self._unwrap(self._client.messages.parse(**self._kwargs(prompt, response_model)))

    async def agenerate(self, prompt, response_model):
        return self._unwrap(await self._aclient.messages.parse(**self._kwargs(prompt, response_model)))


judge = ClaudeJudge()
faithfulness = Faithfulness(llm=judge)
relevancy = AnswerRelevancy(llm=judge, embeddings=ProjectEmbedder())  # see below
result = await faithfulness.ascore(user_input=q, response=answer, retrieved_contexts=chunk_texts)
```

The `calls` attribute is rebuilt as a new tuple on each call, in line with the project's immutability rule; the class still holds state, so ticket 07 may prefer to return usage through a callback instead.

### Embeddings for answer relevancy

- `AnswerRelevancy` needs `embeddings: BaseRagasEmbedding` and rejects anything else (`ragas/metrics/collections/base.py` lines 123 to 130). It generates `strictness` questions (default 3) from the answer, embeds them and the original question, and scores the mean cosine similarity, set to 0 when every generated question is flagged noncommittal (`answer_relevancy/metric.py` lines 112 to 160).
- Ragas ships embedding providers for OpenAI, Google, Hugging Face and LiteLLM (`ragas/embeddings/*_provider.py`; `embedding_factory` examples at `ragas/embeddings/base.py` lines 708 to 715). Hugging Face uses either `huggingface_hub.InferenceClient` (network) or `sentence_transformers.SentenceTransformer` (local, downloads weights on first use), and neither package is a ragas hard dependency (`ragas/embeddings/huggingface_provider.py` lines 49 to 71).
- There is no Voyage provider in ragas 0.4.3. A LangChain embedding such as `langchain-voyageai` is a legacy embedding and fails the `BaseRagasEmbedding` check above.
- `BaseRagasEmbedding` has two abstract methods, `embed_text` and `aembed_text`; `embed_texts` and `aembed_texts` have defaults (`ragas/embeddings/base.py` lines 30 to 80). A subclass that calls whatever embedder Step 5 chooses needs no extra package. The score depends on the embedder, so record its name and version with every answer relevancy result.

## 3. DeepEval with Claude as the judge

- `deepeval.models.AnthropicModel(model="claude-opus-5-5")` works without an adapter. The model registry entry sets `supports_temperature=False`, `supports_structured_outputs=True`, $4 and $20 per million input and output tokens, and deliberately leaves `supports_thinking` unset because the model "Rejects `thinking: {"type": "disabled"}`" (`models/llms/constants.py` lines 766 to 776). With that entry, `_create_kwargs` sends no `temperature` and no `thinking` block, and sends `output_config.format` built with `anthropic.transform_schema` (`models/llms/anthropic_model.py` lines 241 to 292).
- `generation_kwargs` are passed straight into `messages.create`, and an `output_config` in them is merged with the schema format (`anthropic_model.py` lines 264 to 291). That is the place to set effort and a larger `max_tokens` (default 8192, line 33).
- The API key comes from the `api_key` argument or `ANTHROPIC_API_KEY` (`anthropic_model.py` lines 85 to 94, `_build_client`). DeepEval disables the SDK's own retries and retries with tenacity instead (`_client_kwargs`).
- No extra package: `anthropic` is the only addition, and it is already on the approved list.

Sketch (not run):

```python
from deepeval.metrics import FaithfulnessMetric
from deepeval.models import AnthropicModel
from deepeval.test_case import LLMTestCase

judge = AnthropicModel(
    model="claude-opus-5-5",
    generation_kwargs={"max_tokens": 16000, "output_config": {"effort": "medium"}},
)
metric = FaithfulnessMetric(model=judge, penalize_ambiguous_claims=True, include_reason=True, async_mode=False)
metric.measure(LLMTestCase(input=q, actual_output=answer, retrieval_context=chunk_texts))
print(metric.score, metric.reason, metric.input_tokens, metric.output_tokens, metric.evaluation_cost)
```

## 4. Token usage and cost

- Ragas collections path: `InstructorLLM.generate` and `agenerate` call `client.chat.completions.create(..., response_model=...)` and return only the parsed model (`ragas/llms/base.py` lines 1031 to 1125), so token counts are lost. The custom judge in section 2 reads `response.usage` itself.
- Ragas legacy path: `evaluate(..., token_usage_parser=get_token_usage_for_anthropic)` attaches a `CostCallbackHandler`, a LangChain callback that reads `usage.input_tokens` and `usage.output_tokens` from LangChain response metadata (`ragas/cost.py` lines 78 to 110 and 161 onward; `ragas/evaluation.py` lines 221 to 224). It only works with LangChain-wrapped models, so it does not apply to the recommended path.
- DeepEval: each `AnthropicModel` call returns an `EvaluationCost`, a `float` subclass in USD that also carries `input_tokens` and `output_tokens` (`models/utils.py` line 10 onward; `anthropic_model.py` `_parse_message` and `calculate_cost`). Each metric accumulates `evaluation_cost`, `input_tokens` and `output_tokens` (`metrics/base_metric.py` lines 124 to 126 and 188 to 203).
- Thinking is billed as output tokens even when it is not returned, and `usage.output_tokens` includes it. Source: migration guide, "`max_tokens` covers thinking plus text". Both libraries therefore count thinking in output tokens without extra work.
- Neither library counts prompt-cache tokens (`cache_read_input_tokens`, `cache_creation_input_tokens`). The custom judge can record them from `response.usage`.

## 5. Network calls and telemetry

### Ragas 0.4.3

- `ragas/_analytics.py` posts usage events with `requests.post` to `https://t.explodinggradients.com`, timeout 1 second (`USAGE_TRACKING_URL`, `track()`).
- `track()` returns early when `do_not_track()` is true. That reads `RAGAS_DO_NOT_TRACK` and is true only when the value lowercases to `"true"`, so `RAGAS_DO_NOT_TRACK=1` does not opt out. The result is cached with `lru_cache`, so set the variable before the first metric runs (`_analytics.py` lines 41 to 50).
- Importing `ragas._analytics` (pulled in by `ragas.metrics.base` and the LLM and embedding modules) creates a module-level `AnalyticsBatcher(batch_size=10, flush_interval=10)`, which starts a daemon thread, and registers an `atexit` flush (`_analytics.py` lines 287 to 289). The thread sends only when its buffer holds events, so import alone sends nothing. `llm_factory`, `InstructorLLM.generate` and the prompt classes call `track()` directly on use (`ragas/llms/base.py` lines 702 and 1069; `ragas/prompt/*.py`).
- Tracking errors are swallowed by the `@silent` decorator (`_analytics.py` lines 57 to 77), which catches `Exception`. Whether pytest-socket's `SocketBlockedError` is an `Exception` subclass, and so swallowed, is unverified. Set `RAGAS_DO_NOT_TRACK=true` regardless.
- When an event is built, `get_userid()` writes `uuid.json` under `appdirs.user_data_dir("ragas")` (`_analytics.py` lines 80 to 106).
- tiktoken: `ragas/tokenizers.py` sets `DEFAULT_TOKENIZER = _LazyTokenizer()` (line 157), which loads `o200k_base` on first use, not at import (lines 55 to 64). The project vendors only `cl100k_base`, so any ragas code path that counts tokens would try to download `o200k_base`. Faithfulness and answer relevancy in the collections path do not count tokens by grep; unverified at run time.
- A grep of the ragas package for module-level `requests`, `urlopen`, download or `get_encoding` calls found none. Whether its transitive imports (LangChain, datasets) make network calls at import is unverified. Ticket 07 should add a test that imports ragas with sockets blocked, like `tests/test_tokenizer_offline.py`.

### DeepEval 4.2.8

- `import deepeval` calls `autoload_dotenv()` at import time (`deepeval/__init__.py` lines 9 to 12). It reads `.env`, `.env.{APP_ENV}` and `.env.local` from the working directory, or from `ENV_DIR_PATH`, into `os.environ` without overriding existing values. `DEEPEVAL_DISABLE_DOTENV=1` skips it (`deepeval/config/settings.py` lines 257 to 286). In this repo it would read the project's `.env`, which conflicts with invariant 7.
- deepeval registers a pytest plugin through the `pytest11` entry point (`deepeval-4.2.8.dist-info/entry_points.txt`: `deepeval=deepeval.plugins.plugin`). pytest loads it in every run once deepeval is installed, and the plugin imports `deepeval.telemetry`, which triggers the `.env` autoload above. It also prints "Running teardown with pytest sessionfinish..." at the end of every session (`plugins/plugin.py`). Disable it with `-p no:deepeval` in `addopts`, or set `DEEPEVAL_DISABLE_DOTENV=1` before pytest starts.
- Telemetry goes to PostHog at `https://us.i.posthog.com` (`deepeval/telemetry/client.py` line 26). `capture()` and `get_backend()` return a no-op when `DEEPEVAL_TELEMETRY_OPT_OUT` is truthy (`client.py` lines 55 and 117 to 127; settings field at `config/settings.py` line 929). `DEEPEVAL_TELEMETRY_ENABLED` is a deprecated inverted alias, and "any OFF signal wins" (line 935). The pytest plugin opens a telemetry run scope at session start with `skip_if_empty=True`, so a session with no deepeval metrics should send nothing (`plugins/plugin.py` lines 62 to 75; `telemetry/api.py` line 49 onward).
- The PyPI update check (`requests.get("https://pypi.org/pypi/deepeval/json")`) runs only when `DEEPEVAL_UPDATE_WARNING_OPT_IN=1` (`deepeval/__init__.py`, `update_warning_opt_in`). It is off by default.
- Local state, including the anonymous telemetry ID, lives in `~/.deepeval` unless `DEEPEVAL_HOME` is set (`config/settings.py` around line 937).
- No Sentry dependency or import was found in either package.

### Environment variables to set

| Variable | Value | Effect |
|---|---|---|
| `RAGAS_DO_NOT_TRACK` | `true` | Ragas sends no analytics. Must be the string `true`. |
| `DEEPEVAL_TELEMETRY_OPT_OUT` | `1` | DeepEval uses the no-op telemetry backend. |
| `DEEPEVAL_DISABLE_DOTENV` | `1` | DeepEval does not read `.env` files at import. |

## 6. ID-based context precision and recall in Ragas

- Ragas has `IDBasedContextPrecision` and `IDBasedContextRecall` in the legacy metrics module (`ragas/metrics/_context_precision.py` line 251, `ragas/metrics/_context_recall.py` line 227). They read `retrieved_context_ids` and `reference_context_ids` from a `SingleTurnSample`.
- Both are set-based and ignore rank: precision is hits divided by the number of distinct retrieved IDs, and recall is hits divided by the number of distinct reference IDs; an empty reference set gives NaN (`_context_recall.py` lines 260 to 278, `_context_precision.py` lines 290 to 304). The project's own chunk-ID metrics can keep rank-aware definitions; these classes are only a cross-check.
- The collections versions of context precision and recall (`ragas/metrics/collections/context_precision/metric.py`) are LLM-judged, not ID-based.

## 7. DeepEval faithfulness compared with Ragas faithfulness

| | Ragas 0.4.3 `Faithfulness` (collections) | DeepEval 4.2.8 `FaithfulnessMetric` |
|---|---|---|
| Claim step | Splits the answer into atomic statements, given the question | Extracts claims from the answer alone |
| Context step | Joins retrieved contexts with newlines and judges against the raw text | First asks the LLM to extract "truths" from the context, then judges claims against those truths |
| Verdict | 1 if the statement "can be directly inferred based on the context", else 0 | `yes`, `no` or `borderline`. `no` only if the context "DIRECTLY CONTRADICTS the claim"; claims "not backed up by context" are `borderline` |
| Score | Supported statements over all statements | Passing verdicts over all verdicts. By default `yes` and `borderline` both pass; with `penalize_ambiguous_claims=True` only `yes` passes |
| No claims | NaN | 1.0 |
| LLM calls per answer | 2 | 3, plus 1 for the reason when `include_reason=True` |
| Reasons | Per-statement `reason` field | Per-claim reasons for `no` and `borderline`, plus a summary reason and `verbose_logs` |

Sources: `ragas/metrics/collections/faithfulness/metric.py` lines 97 to 191 and `faithfulness/util.py` lines 35 and 86; `deepeval/metrics/faithfulness/faithfulness.py` lines 113 to 160, 259 to 347 and 408 to 414; `deepeval/metrics/utils/qag.py` lines 113 to 138; the `faithfulness_verdicts_guidelines_text_only` fragment in `deepeval/templates/metrics/templates.json`; `metrics/faithfulness/templates/generate_truths.txt` and `generate_verdicts.txt`.

With default settings, DeepEval measures "nothing in the answer contradicts the context". An answer padded with unsupported figures scores 1.0, which is the failure a filings Q&A benchmark most needs to catch. With `penalize_ambiguous_claims=True` it approaches Ragas's support check, but judges against an LLM summary of the context instead of the context itself, which adds a lossy step and a third call. Two LLM judges using the same model on near-identical prompts are not an independent second opinion.

Recommendation: drop DeepEval. Use Ragas faithfulness as the faithfulness metric, and use the project's own citation check (invariant 3) as the non-LLM cross-check. Reasons: (a) the default metric answers a weaker question, (b) the penalized variant mostly duplicates Ragas at about 1.5 to 2 times the judge cost, and (c) installation alters every pytest run (the plugin, the `.env` autoload, four extra pytest plugins including pytest-asyncio) and adds PostHog, OpenTelemetry and gRPC. DeepEval is listed in the CLAUDE.md stack, so this needs the user to reopen that decision. If the user keeps it: pin 4.2.8, set `penalize_ambiguous_claims=True`, add `-p no:deepeval` to pytest `addopts`, set the three environment variables above, and report it in its own column.

## 8. Determinism controls

- Claude Opus 5.5 accepts no sampling controls: `temperature`, `top_p` and `top_k` must be omitted or left at their defaults, and SDK v1.0 and later raises `TypeError` if they are passed. The guide notes that "`temperature = 0` ... never guaranteed identical outputs on prior models". Source: https://platform.claude.com/docs/en/models/opus-5-5/migration-guide.
- Thinking is always on and cannot be disabled; `effort` (`low` to `max`, default `medium`) is the only depth control. Source: https://platform.claude.com/docs/en/about-claude/models/overview and the migration guide. Set effort explicitly and record it with every result.
- Forced `tool_choice` and assistant prefill are rejected; structured outputs through `output_config.format` (or `messages.parse`) are the supported way to get JSON. Source: migration guide.
- Ragas: the default instructor path hard-codes `temperature=0.01` and `top_p=0.1` (section 2), so it cannot run on this model without the custom class. `AnswerRelevancy` sends the same prompt `strictness` times (`answer_relevancy/metric.py` line 116) and relies on sampling variation between them; with no temperature control that variation is whatever the model's default produces.
- DeepEval: `temperature` is sent only when explicitly set and the model's registry entry allows it; for `claude-opus-5-5` it never is (section 3).
- Repeatability comes from caching, not sampling settings: store each judge response keyed by model ID, effort, library version and a hash of the prompt, and reuse it on reruns. Ragas has a `DiskCacheBackend` for `llm_factory` LLMs (`ragas/cache.py`), but the custom judge would need its own cache. Report run-to-run variance from at least one repeated run.
- Refusal fallbacks (`fallbacks` parameter) would let a different model answer silently. Do not enable them for the judge; treat `stop_reason == "refusal"` as an error and count it.

## 9. The model ID `claude-opus-5-5`

- `claude-opus-5-5` is the Claude API ID and alias for Claude Opus 5.5, listed as a current model. It is a pinned snapshot ("Every Claude model ID is a pinned snapshot, including the dateless IDs"), with retirement "Not sooner than September 22, 2027". Pricing is $4 per input MTok and $20 per output MTok; context 1M tokens; max output 128K tokens; thinking "Adaptive (always on)"; default effort `medium`. Source: https://platform.claude.com/docs/en/about-claude/models/overview, fetched 2026-10-05.
- Parameter restrictions documented for it: no `temperature`, `top_p` or `top_k` other than defaults; no `thinking: {"type": "disabled"}` and no `budget_tokens`; no forced `tool_choice` (`any` or `tool`); no assistant prefill; `max_tokens` covers thinking plus text; `output_format` is deprecated in favour of `output_config.format`. Source: https://platform.claude.com/docs/en/models/opus-5-5/migration-guide.
- Because the ID is a pinned snapshot, pinning the model is a matter of recording `claude-opus-5-5` and the effort level with every result. The response's `model` field can be logged as a check.

## Open items for ticket 07

- Confirm with one live call that `messages.parse` accepts `output_config={"effort": ...}` alongside `output_format`.
- Confirm that the Ragas response models pass Anthropic's structured-output schema rules.
- Add an offline import test for ragas (and deepeval if kept) with sockets blocked.
- Choose the embedder wrapper once Step 5 picks the embedding model.
