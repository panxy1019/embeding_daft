# LLM Multi-Backend Refactor Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Remove LiteLLM from mandatory runtime dependencies while preserving the current `ahvn.utils.llm.LLM` contract, keeping `openai` required, making `portkey` the shipped default engine, and keeping `portkey`, `bifrost`, and `litellm` equivalent peer engines in Python code while treating `bifrost` only as an externally deployed running server.

**Architecture:** Introduce a real engine-aware transport layer beneath `LLM`. Separate logical model and provider resolution from engine-specific request shaping and response normalization. Keep wrapper-level features in AgentHeaven, make `openai` the required transport baseline, make `portkey` the default-config engine, keep `litellm` optional, and model `bifrost` only as an external OpenAI-compatible server endpoint.

**Tech Stack:** `openai`, optional `portkey-ai` helper, optional `litellm`, externally deployed Bifrost server, Tenacity, existing cache layer, existing `ToolSpec` tool execution and repair pipeline.

---

## 1. Executive Summary

This refactor is not a simple dependency deletion. LiteLLM is currently embedded into:

- config materialization
- request building
- retryable exception selection
- message normalization
- stream chunk parsing
- embeddings
- unit tests

The updated requirement set now settles the main deployment decision:

- `openai` must remain required
- `portkey` should become the shipped default engine
- `bifrost` should stay supported, but only as a manually deployed external server
- `litellm` should stay optional
- in Python code, `portkey`, `bifrost`, and `litellm` should satisfy the same backend contract and behave as equivalent peer engines from the application perspective

This changes the earlier plan materially. AgentHeaven should no longer attempt to install, bootstrap, or manage Bifrost. Instead:

- deployment is responsible for downloading and starting Bifrost
- AgentHeaven only talks to an already-running Bifrost server through an OpenAI-compatible endpoint
- the shipped default config should target Portkey rather than Bifrost

This removes the highest operational risk from the Python package while still preserving Bifrost as a first-class backend option.

## 2. Verified Compatibility Findings

### 2.1 Engine comparison

| Engine | Python install path | Python usage path | External runtime required | Role in the plan | Main risk |
| --- | --- | --- | --- | --- | --- |
| `openai` | Yes, `pip install openai` | Native SDK with `base_url`, `extra_headers`, `extra_body`, `extra_query` | No | Required baseline transport and fallback engine | OpenAI-compatible providers do not all guarantee full Responses API parity |
| `portkey` | Optional helper package, but engine can run on top of required `openai` transport | OpenAI-compatible gateway usage plus Portkey-specific headers or helper library | No local process required | Shipped default engine | Portkey-specific routing headers and metadata must stay behind the adapter |
| `litellm` | Yes, `pip install litellm` | Native Python SDK and optional proxy mode | No | Optional compatibility peer engine | Must stay optional and lazy-loaded |
| `bifrost` | No meaningful in-process Python package path from current evidence | OpenAI-compatible HTTP endpoint via required `openai` transport | Yes, externally deployed server | Supported external peer engine only | Manual deployment, availability, and version drift are outside AgentHeaven's process lifecycle |

### 2.2 Why `openai` remains the baseline transport

`openai` is still the safest mandatory foundation because it already provides the transport primitives that the engine layer needs:

- `base_url`
- `extra_headers`
- `extra_body`
- `extra_query`
- timeouts and retries
- sync and async streaming
- embeddings

This makes it the correct baseline for:

- `openai` direct
- `portkey`
- `bifrost`

The common denominator for v1 should remain Chat Completions compatible transport with AgentHeaven-side normalization. That is the broadest reliable intersection across the engines we need to support.

### 2.3 Why `portkey` becomes the default

Portkey is the best default under the updated constraint set because it gives the package a gateway-capable default without forcing AgentHeaven to manage an external service lifecycle. It supports:

- OpenAI-compatible routing
- provider-specific headers
- gateway policy and observability
- Python-side usage without adding a local managed runtime to AgentHeaven

This makes Portkey a better shipped default than Bifrost once Bifrost lifecycle management is explicitly removed from scope.

### 2.4 Why `bifrost` remains supported

Bifrost still has a strong normalization story and remains worth supporting for deployed environments that already run it. The difference is purely operational:

- AgentHeaven will not own Bifrost deployment
- AgentHeaven will only consume a configured and already-running Bifrost endpoint

That keeps Bifrost in the backend matrix without turning deployment automation into a Python package requirement.

## 3. Requirement Reconciliation And Deployment Assumptions

### 3.1 Final assumption set

This plan assumes the following are true:

- `openai` is mandatory
- `portkey` is the default-config engine
- `bifrost` is never installed, launched, or supervised by AgentHeaven
- `bifrost` is available only when the deployer provides a running server and corresponding config
- `litellm` is optional

### 3.2 Python-side equivalence definition

In Python code, these three engines must be treated as equal peers:

- `portkey`
- `bifrost`
- `litellm`

Equal here means they must all satisfy the same AgentHeaven backend contract:

- chat
- stream
- tools
- structured output
- reasoning or think channel
- embeddings
- usage reporting
- sync and async behavior

Equal does not mean identical deployment or installation mechanics.

### 3.3 Explicit non-goal

AgentHeaven must not implement any of the following for Bifrost:

- auto-download
- local installation
- subprocess startup
- health-check orchestration for a bundled runtime
- shutdown or lifecycle supervision

Those belong to deployment and operations outside the Python package.

### 3.4 Approval gates before implementation

- Approve `portkey` as the shipped default engine.
- Approve `bifrost` as an external running-server assumption only.
- Approve Chat Completions compatible transport as the v1 common denominator instead of OpenAI Responses API.
- Approve config migration from overloaded `backend` semantics to explicit `engine` semantics.
- Approve explicit capability errors when an engine cannot preserve a requested feature faithfully.

## 4. Current State In AgentHeaven

### 4.1 Config coupling

Current config semantics are LiteLLM-specific:

- `llm.backend` currently means LiteLLM provider prefix, not engine selection
- `llm.providers.*.backend` stores LiteLLM routing identifiers
- `spec.materialize()` prefixes backend into model to form LiteLLM-ready identifiers

This means the current name `backend` is already taken and cannot be reused as the future engine selector without migration logic.

### 4.2 Runtime coupling

The current runtime assumes LiteLLM in all major paths:

- sync chat and stream
- async chat and stream
- sync embeddings
- async embeddings
- retryable exception mapping
- LiteLLM `Message` normalization
- stream chunk parsing against LiteLLM objects

### 4.3 Test coupling

Current unit tests patch LiteLLM internals directly. That means tests also need architectural migration, not just additional cases.

## 5. Naming And Interface Changes

### 5.1 Public Python interface

Add explicit engine awareness while keeping the current facade stable.

Proposed direction:

```python
LLM(
    preset="chat",
    model="sonnet",
    provider="anthropic",
    engine="portkey",
    base_url="...",
    extra_headers={...},
    extra_body={...},
)
```

Rules:

- `engine` selects the runtime transport engine
- `provider` remains the logical upstream provider or route identity
- `model` remains the provider-native identifier chosen for that engine
- omitting `engine` uses resolver policy and shipped defaults

### 5.2 Config naming changes

Required direction:

- `engine`: real engine selector
- `provider`: logical upstream vendor or route identity
- `model`: resolved model identifier for the chosen provider and engine
- `base_url`: canonical transport URL field
- `headers`, `extra_headers`, `extra_body`, `extra_query`: transport extension fields

Deprecated compatibility inputs:

- `backend`: accepted only as a legacy alias during migration
- `api_base`: accepted only as a legacy alias of `base_url`

### 5.3 Internal interfaces

Introduce an engine contract under `src/ahvn/utils/llm/backends/`.

Required internal components:

- request builder
- response normalizer
- stream chunk normalizer
- capability profile
- retryable exception mapper
- embedding adapter

No Bifrost runtime manager should exist in this design.

## 6. Config Model Direction

### 6.1 Does the default LLM config need to change?

Yes. It must change materially.

The current `default_config.yaml` cannot represent the new architecture safely because:

- it states LiteLLM is the only supported backend
- it uses `backend` for LiteLLM provider prefixes
- it uses `api_base`, while the future shared transport should converge on `base_url`
- it does not distinguish shared transport settings from engine-specific settings
- it cannot cleanly express “external Bifrost server” versus “in-package engine selection” yet

### 6.2 Can the engines share the same config?

Partially, not fully.

Shared envelope that all OpenAI-compatible engines can use:

- `base_url`
- `api_key`
- `timeout`
- `headers`
- `extra_headers`
- `extra_body`
- `extra_query`
- proxies

Engine-specific overlays still need to exist:

- `portkey`: virtual keys, provider headers, config IDs, routing metadata
- `bifrost`: external server URL and deployment assumptions only
- `litellm`: provider prefix and LiteLLM-specific compatibility flags

Therefore, the correct design is a shared transport envelope with engine-specific overlays, not one flat identical block for all engines.

### 6.3 Proposed config sketch

```yaml
llm:
  default_engine: portkey
  default_provider: openrouter

  default_args:
    seed: 42
    timeout: 120
    repair_tool_calls: true
    enforce_non_stream_structured: false

  engines:
    openai:
      enabled: true
    portkey:
      enabled: true
      base_url: "https://api.portkey.ai/v1"
      headers:
        x-portkey-api-key: "<PORTKEY_API_KEY>"
    bifrost:
      enabled: false
      externally_managed: true
      base_url: "<RUNNING_BIFROST_OPENAI_URL>"
    litellm:
      enabled: false

  providers:
    anthropic:
      engine_preferences: [portkey, bifrost, litellm]
    deepseek:
      engine_preferences: [openai, portkey, bifrost, litellm]
      base_url: "https://api.deepseek.com/beta"
    ollama:
      engine_preferences: [openai, litellm]
      base_url: "http://localhost:11434/v1"
```

This is directionally correct, not a final schema.

### 6.4 Legacy migration rules

- old `backend` values map into provider or engine hints depending on context
- old `api_base` maps to `base_url`
- old presets continue to resolve without user edits where possible
- ambiguous legacy configurations must raise targeted migration errors instead of guessing

## 7. Backend Usage Model

### 7.1 Default usage

Most users should keep using the current facade:

```python
llm = LLM(preset="chat")
```

Resolver behavior:

1. respect explicit `engine` if provided
2. otherwise try per-model or per-provider engine preferences
3. otherwise use the shipped default engine `portkey`
4. if the selected engine is unavailable for the requested feature set, apply explicit switch guidance
5. otherwise fall back to `openai` direct where appropriate

### 7.2 Explicit engine usage

```python
llm = LLM(model="claude-sonnet-4-6", provider="anthropic", engine="portkey")
llm = LLM(model="claude-sonnet-4-6", provider="anthropic", engine="bifrost", base_url="http://host:8080/openai")
llm = LLM(model="deepseek-chat", provider="deepseek", engine="openai")
llm = LLM(model="gpt-5.4", provider="openai", engine="litellm")
```

### 7.3 Engine-specific explicit guidance

- Anthropic reasoning or thinking should prefer `portkey`, allow `bifrost`, and allow `litellm` fallback.
- Local `extra_body.think` extensions should prefer `openai` direct or `litellm`.
- DeepSeek reasoning should allow `openai` direct, `portkey`, and `bifrost` after adapter verification.
- `bifrost` selection should fail fast if no external server URL is configured.

## 8. Feature Contract And Capability Policy

### 8.1 Product behaviors that must remain stable

- sync and async chat
- streaming aggregation
- tool calling
- tool-call repair
- structured output handling
- reasoning or think extraction
- embeddings
- usage collection
- timeout and retry handling
- cache integration
- provider-specific request extensions where explicitly supported

### 8.2 No-silent-degradation policy

Rules:

- preserve a feature natively when the engine can do it faithfully
- emulate only wrapper-level behaviors in AgentHeaven
- reject any unsafe or lossy engine substitution with explicit switch guidance

### 8.3 Peer-equivalence definition

`portkey`, `bifrost`, and `litellm` are equal peers only at the AgentHeaven Python contract level.

That means each must preserve or explicitly reject the same product-level feature set:

- chat
- stream
- tools
- structured output
- reasoning channel
- embeddings
- usage

It does not mean they share identical installation or deployment mechanics.

## 9. Proposed Internal Architecture

### 9.1 Directory direction

Proposed new files:

- `src/ahvn/utils/llm/backends/base.py`
- `src/ahvn/utils/llm/backends/openai_transport.py`
- `src/ahvn/utils/llm/backends/openai_direct.py`
- `src/ahvn/utils/llm/backends/portkey.py`
- `src/ahvn/utils/llm/backends/litellm.py`
- `src/ahvn/utils/llm/backends/bifrost.py`

### 9.2 Shared backend contract

Required methods or equivalents:

- `chat()`
- `stream_chat()`
- `achat()`
- `astream_chat()`
- `embed()`
- `aembed()`
- `normalize_response()`
- `normalize_stream_chunk()`
- `retryable_exceptions()`
- `capability_profile()`

### 9.3 Wrapper responsibilities that stay in `LLM`

- tool execution
- tool-call repair
- cache keys and cache integration
- usage aggregation shape
- include filtering
- fallback and switch guidance

### 9.4 Shared OpenAI-compatible transport base

`openai`, `portkey`, and `bifrost` should share a common transport base using the mandatory `openai` dependency. This keeps the Python transport surface aligned while allowing engine-specific request overlays. `litellm` remains the only non-OpenAI transport implementation.

## 10. Affected Files

### 10.1 Packaging and dependency files

- `requirements.txt`
- `environment.yml`
- `environment-full.yml`
- `pyproject.toml`
- `src/ahvn/utils/basic/deps_utils.py`

### 10.2 Core LLM implementation files

- `src/ahvn/utils/llm/spec.py`
- `src/ahvn/utils/llm/base.py`
- `src/ahvn/utils/llm/llm_utils.py`
- new backend files under `src/ahvn/utils/llm/backends/`

### 10.3 Config and docs

- `src/ahvn/resources/configs/default_config.yaml`
- docs and README sections that describe LLM configuration
- deployment documentation for external Bifrost setup

### 10.4 Tests

- `tests/unit/llm/test_llm_base.py`
- `tests/unit/llm/test_llm_utils.py`
- new adapter and config migration tests under `tests/unit/llm/`

### 10.5 Downstream regression targets

- `src/ahvn/cli/chat_cli.py`
- `src/ahvn/agent/base.py`
- `src/ahvn/tool/llm/ask.py`
- `src/ahvn/tool/llm/toolkit.py`
- `src/ahvn/utils/exts/autocode.py`
- `src/ahvn/utils/exts/autofunc.py`
- `src/ahvn/utils/exts/autotask.py`
- `src/ahvn/utils/prompt/translate.py`
- `src/ahvn/utils/vdb/vdb_utils.py`

## 11. Implementation Tasks

### Task 1: Freeze dependency boundaries

**Files:**

- Modify: `requirements.txt`
- Modify: `environment.yml`
- Modify: `environment-full.yml`
- Modify: `pyproject.toml`
- Modify: `src/ahvn/utils/basic/deps_utils.py`

**Steps:**

1. Remove `litellm` from mandatory dependency declarations.
2. Keep `openai` in the mandatory dependency path.
3. Decide whether `portkey-ai` remains an optional helper extra or is not required at all.
4. Keep `litellm` optional.
5. Do not add any Bifrost package dependency.
6. Update lazy dependency install hints and error messages.

**Verification direction:**

- import AgentHeaven without LiteLLM installed
- select `openai` engine without LiteLLM installed
- select `portkey` engine with only the required transport path available
- selecting `litellm` without the extra installed must produce a clean dependency error

### Task 2: Introduce explicit engine semantics

**Files:**

- Modify: `src/ahvn/utils/llm/spec.py`
- Modify: `src/ahvn/resources/configs/default_config.yaml`
- Add or modify config migration tests under `tests/unit/llm/`

**Steps:**

1. Add `engine` to the resolved LLM spec.
2. Preserve legacy `backend` as a deprecated input alias only.
3. Preserve legacy `api_base` as a deprecated alias of `base_url`.
4. Stop using `backend` to build engine identity.
5. Add config for external Bifrost endpoint selection, not runtime lifecycle.
6. Add resolver warnings or errors for ambiguous legacy combinations.

**Verification direction:**

- old preset-only configs still resolve
- legacy `backend` configs map deterministically
- ambiguous configs fail explicitly
- Bifrost config without endpoint information fails clearly

### Task 3: Extract backend contracts

**Files:**

- Add: `src/ahvn/utils/llm/backends/base.py`
- Add: `src/ahvn/utils/llm/backends/openai_transport.py`
- Modify: `src/ahvn/utils/llm/base.py`
- Modify: `src/ahvn/utils/llm/llm_utils.py`

**Steps:**

1. Move request and response normalization boundaries behind a backend interface.
2. Keep tool repair and cache behavior at the wrapper level.
3. Extract retryable exception selection out of LiteLLM-only helpers.
4. Remove LiteLLM `Message` type assumptions from shared normalization logic.

**Verification direction:**

- `LLM` facade still exposes the same public methods
- shared code no longer imports LiteLLM unless `litellm` engine is selected

### Task 4: Implement `openai` direct backend

**Files:**

- Add: `src/ahvn/utils/llm/backends/openai_direct.py`
- Modify: `src/ahvn/utils/llm/base.py`
- Add tests under `tests/unit/llm/`

**Steps:**

1. Implement sync and async chat.
2. Implement sync and async streaming normalization.
3. Implement sync and async embeddings.
4. Support `base_url`, `extra_headers`, `extra_body`, `extra_query`, proxies, and timeout.
5. Make `openai` the first engine that works without LiteLLM present.

**Verification direction:**

- core LLM path works in a LiteLLM-free environment
- stream and non-stream shapes match the current wrapper contract

### Task 5: Implement `portkey`

**Files:**

- Add: `src/ahvn/utils/llm/backends/portkey.py`
- Modify: `src/ahvn/resources/configs/default_config.yaml`
- Add tests under `tests/unit/llm/`

**Steps:**

1. Build on top of the shared OpenAI-compatible transport base.
2. Map Portkey headers, config IDs, and provider routing into transport overlays.
3. Validate streaming, tools, structured output, reasoning extraction, and embeddings.
4. Make Portkey the shipped default engine in config and examples.
5. Mark unsupported or unverified combinations explicitly.

**Verification direction:**

- Portkey preserves the same wrapper contract as `openai`
- engine-specific routing metadata does not leak into the common wrapper interface

### Task 6: Implement optional `litellm`

**Files:**

- Add: `src/ahvn/utils/llm/backends/litellm.py`
- Modify: `src/ahvn/utils/llm/llm_utils.py`
- Modify: `src/ahvn/utils/llm/base.py`
- Add tests under `tests/unit/llm/`

**Steps:**

1. Move all LiteLLM-specific loading and exception logic into the optional adapter.
2. Preserve existing LiteLLM-only compatibility behavior there.
3. Keep LiteLLM import strictly lazy.
4. Make LiteLLM selection fail cleanly when the extra is not installed.

**Verification direction:**

- core import path never touches LiteLLM
- legacy LiteLLM behavior remains available when optional extra is installed

### Task 7: Implement external `bifrost`

**Files:**

- Add: `src/ahvn/utils/llm/backends/bifrost.py`
- Modify: `src/ahvn/resources/configs/default_config.yaml`
- Add tests under `tests/unit/llm/`

**Steps:**

1. Implement Bifrost request overlays on the shared OpenAI-compatible base.
2. Require an explicitly configured running Bifrost endpoint.
3. Codify verified provider mappings for Anthropic, DeepSeek, OpenAI-compatible providers, and local providers where validated.
4. Fail explicitly when Bifrost cannot preserve a requested provider-specific extension.
5. Document that deployment, installation, and startup happen outside AgentHeaven.

**Verification direction:**

- Bifrost adapter fails clearly when no running endpoint is configured
- Bifrost adapter works against a configured external server
- no runtime-management code is introduced

### Task 8: Rewire default config and resolver policy

**Files:**

- Modify: `src/ahvn/resources/configs/default_config.yaml`
- Modify: `src/ahvn/utils/llm/spec.py`
- Add tests under `tests/unit/llm/`

**Steps:**

1. Set `portkey` as the default engine in shipped config.
2. Keep `openai` as the default fallback transport.
3. Mark `portkey`, `bifrost`, and `litellm` as equal peer engines in Python code.
4. Add engine preference rules by provider and model family.
5. Preserve local-model pathways that need `extra_body` pass-through.

**Verification direction:**

- default config resolves deterministically
- local-model presets are not silently broken by engine defaults
- Bifrost remains opt-in and externally configured

### Task 9: Refactor tests into contract and adapter layers

**Files:**

- Modify: `tests/unit/llm/test_llm_base.py`
- Modify: `tests/unit/llm/test_llm_utils.py`
- Add: `tests/unit/llm/backends/`

**Steps:**

1. Replace direct LiteLLM monkeypatching with backend-contract fakes.
2. Keep wrapper-level tests for tool repair, include filtering, usage aggregation, and embedding batching.
3. Add adapter-specific tests for each engine.
4. Add config migration tests.

**Verification direction:**

- wrapper tests pass regardless of engine implementation choice
- engine-specific tests isolate transport assumptions cleanly

### Task 10: Docs, migration notes, and final verification

**Files:**

- Modify LLM docs and README sections as needed
- Modify: `src/ahvn/resources/configs/default_config.yaml`
- Potentially modify tutorials that show LLM configuration

**Steps:**

1. Document when to choose `portkey`, `openai`, `bifrost`, or `litellm`.
2. Document that Bifrost must be manually downloaded, deployed, and started outside AgentHeaven.
3. Document migration from `backend` to `engine` and from `api_base` to `base_url`.
4. Add explicit switch-guidance examples for unsupported engine-feature combinations.
5. Run focused verification.

**Verification direction:**

- `bash scripts/test.bash tests/unit/llm`
- targeted import and smoke checks for each install profile
- downstream regression checks for primary `LLM` consumers

## 12. Risk Report

### 12.1 High risk

**Legacy `backend` migration**

- Reason: current configs already depend on `backend` meaning provider prefix
- Impact: silent routing bugs if semantics are mixed
- Mitigation: `engine` introduction, strict migration rules, ambiguity errors

**Provider-specific extension pass-through**

- Reason: local presets already rely on `extra_body.think`
- Impact: silent loss of local-model behavior
- Mitigation: explicit capability profiles and engine-switch guidance

### 12.2 Medium risk

**External Bifrost deployment drift**

- Reason: deployment owns install, version, startup, and availability
- Impact: environment-specific failures outside the Python process
- Mitigation: clear config validation, explicit docs, integration checks against running servers

**Stream normalization**

- Reason: current code expects LiteLLM chunk shapes
- Impact: text, reasoning, tool-call, and usage aggregation regressions
- Mitigation: explicit normalized chunk contract and engine-specific tests

**Portkey and Bifrost route metadata differences**

- Reason: headers and route descriptors differ by engine
- Impact: wrappers accidentally leak engine-specific concepts
- Mitigation: keep routing overlays behind adapters only

### 12.3 Low risk

**LiteLLM optionalization**

- Reason: dependency registry already supports optional lazy loading patterns
- Impact: mainly packaging and import path cleanup
- Mitigation: move all LiteLLM logic into the optional adapter cleanly

## 13. Coverage Plan

### 13.1 Contract coverage

Must verify the same product contract across all engines where applicable:

- sync chat
- async chat
- sync stream
- async stream
- tool calls
- tool-call repair
- structured output handling
- reasoning or think extraction
- embeddings
- usage shape

### 13.2 Migration coverage

- legacy `backend` configs
- legacy `api_base` configs
- mixed legacy and new fields
- missing optional dependencies
- engine selection fallback order

### 13.3 Install and environment coverage

Required verification profiles:

- core only: `openai` installed, LiteLLM absent
- core plus optional Portkey helper path if retained
- core plus `litellm`
- core plus external Bifrost server integration
- full developer install

### 13.4 Downstream regression coverage

At minimum, regression-check:

- CLI chat path
- agent base path
- LLM tool wrappers
- auto-code and auto-task helpers
- prompt translation
- vector-db embedding helper flows

## 14. Proposed Execution Order

1. Freeze packaging and dependency boundaries.
2. Introduce `engine` semantics and config migration.
3. Extract the shared backend contract.
4. Make `openai` direct the first LiteLLM-free working engine.
5. Implement `portkey` and make it the default config target.
6. Implement optional `litellm` and external `bifrost` adapters.
7. Finish docs, regression checks, and migration notes.

## 15. Stop Conditions

Do not start implementation until these are explicitly approved:

- `portkey` is the shipped default engine.
- `bifrost` is external-only and not managed by AgentHeaven.
- `engine` replaces overloaded `backend` semantics.
- v1 uses Chat Completions compatible transport as the common denominator.
- unsupported engine-feature combinations fail explicitly instead of silently downgrading.

If any of those are rejected, this plan must be revised before code changes begin.