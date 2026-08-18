# Multi-Provider + Model Configuration — Design Spec

**Date:** 2026-08-17
**Status:** Approved design, pending implementation plan
**Branch:** `worktree-multi-provider-model-config`
**Scope:** Cut Muldro over from Claude-only to a configurable multi-provider model layer, and introduce end-to-end model configuration (backend + UI) where none exists today.

---

## 1. Goal & Non-Goals

### Goal

Today Muldro is hardwired to Anthropic: a fixed `tier → Claude model ID` map (`config/models.py`), a single `ChatAnthropic` build leaf (`llm/model_factory.py`), and an Anthropic-only reasoning-parameter surface (`deep_runtime/_thinking.py`). Nothing about model choice is configurable at runtime — it is code + env only.

This spec makes the model layer **provider-agnostic and configurable**:

1. **Cutover** — any LangChain-supported provider (Anthropic, OpenAI, Google Gemini, local via Ollama/vLLM, gateways like Azure/Bedrock/OpenRouter) can back any logical tier, built through LangChain's `init_chat_model`.
2. **Configuration** — the `tier → (provider, model, params)` mapping, optional per-agent overrides, and provider credentials become **workspace-scoped data**, editable through a new Settings tab and a backend API, with credentials encrypted at rest.
3. **Behavior-preserving** — on ship, the seeded default reproduces today's exact models and parameters. The cutover changes the *path*, not the *behavior*, until a user reconfigures.

### Non-Goals (explicitly out of scope)

- **The agent-topology cutover** (collapsing the 6 chat agents into a single deep lead + read-only subagent). That is a **separate spec**. This spec is written **topology-agnostic**: it is driven by the tier mapping and whatever roles exist in the registry, so it lands correctly whether the agent set is 6, or lead+4, or anything between.
- **Full multi-tenant BYOK admin UI.** The data model is workspace-scoped from day one, but this cutover's UI targets the primary workspace's configuration. Per-workspace tenant management is a later UI flip, not a schema rebuild.
- **Automatic model routing / fallback chains / cost-based selection.** The resolver picks the configured model deterministically. Fallback logic can be layered later.

---

## 2. Decisions (resolved forks)

| # | Fork | Decision |
|---|------|----------|
| 1 | Which providers / driver | **All** — general abstraction over every LangChain-supported provider, motivated by cost, capability, privacy, and enterprise BYO-key. Built on `init_chat_model`. |
| 2 | Config scope + credentials | **Layered, pragmatic** — config stored as **workspace-scoped data** with **encrypted credentials** from the start; this cutover's UI targets the primary workspace. Per-workspace override is a later UI flip. |
| 3 | Config granularity | **Tiers as the spine + optional per-agent override.** Backend-complete; UI shows the 3 tier mappings prominently, per-agent overrides behind an "Advanced" disclosure. |
| 4 | Coupling to agent cutover | **Separate specs.** This spec is topology-agnostic (tier-driven, registry-enumerated) and lands independently of the agent merge. |
| 5 | Provider-dispatch mechanism | **`init_chat_model` + a per-model capability map** (not a per-provider builder registry, not a LiteLLM proxy — the proxy would flatten away the Anthropic thinking + prompt-caching features the agents depend on). |

---

## 3. Architecture

The cutover replaces two hardcoded facts — "tier → Claude model ID" and "how to build a `ChatAnthropic`" — with a resolved pipeline:

```
role/tier + workspace_id
        │
        ▼
   ModelResolver ── reads ──> model_bindings        (DB, workspace-scoped)  ← tier defaults + per-agent/utility overrides
        │          ── reads ──> MODEL_CATALOG        (code)                  ← capabilities + cost  (the "capability map")
        │          ── reads ──> provider_credentials (DB, encrypted)         ← BYO keys / env fallback
        ▼
   ResolvedModel  { provider, model_id, api_key, base_url, kwargs }
        │
        ▼
   build_langchain_model ── init_chat_model(model_id, model_provider=provider, **kwargs) ──> BaseChatModel
        │
        ├──> deep_runtime.build_chat_model   (agents / lead — LangGraph/deepagents)
        └──> build_utility_model             (complete_text — assessors, extraction, classifier)
```

**Principle — code vs. data.** The *capability facts* (does this model accept `temperature`? how does it express reasoning effort? does it support prompt caching?) live in **code** (`model_catalog.py`), versioned and unit-testable. The *choices* (which model backs a tier, whose key) live in **workspace-scoped DB data**. This mirrors the existing `tools/catalog.py → tool_definitions` seed pattern.

**Principle — topology-agnostic resolution.** The resolver resolves whatever it is handed (a tier, or an agent whose tier it looks up). It does not enumerate a fixed agent list, so a change in the agent topology needs new binding *rows*, never a schema or resolver change.

---

## 4. Data Model

### 4.1 Code — `src/config/model_catalog.py` (the capability map)

Source of truth for provider/model capabilities. Replaces the hardcoded `MODEL_TIERS`/`MODEL_TIER_IDS` dicts and the Anthropic-only branching in `_thinking.py`.

```python
@dataclass(frozen=True)
class ModelSpec:
    provider: str                 # "anthropic" | "openai" | "google_genai" | "ollama" | ...
    model_id: str                 # "claude-opus-4-8", "gpt-5", "gemini-2.x", ...
    display_name: str
    thinking_style: str           # "anthropic_adaptive" | "anthropic_legacy" | "openai_effort" | "gemini" | "none"
    accepts_temperature: bool
    supports_prompt_cache: bool
    context_window: int
    input_cost_per_1k: float      # feeds BudgetTracker cost attribution
    output_cost_per_1k: float
    suggested_tier: str           # "reasoning" | "balanced" | "fast"

MODEL_CATALOG: dict[str, list[ModelSpec]]   # provider -> models
```

### 4.2 DB — two workspace-scoped tables (Alembic migration)

**`provider_credentials`**
- PK/unique: `(workspace_id, provider)`. `workspace_id IS NULL` = deployment-default row.
- `encrypted_api_key` (ciphertext), `base_url` (nullable — Ollama/Azure/OpenAI-compatible endpoints), `extra_config` JSONB (org id, region, etc.), `status` (`untested` | `valid` | `invalid`), `enabled`, timestamps.

**`model_bindings`**
- PK/unique: `(workspace_id, scope_type, scope_key)`. `workspace_id IS NULL` = deployment-default row.
- `scope_type` ∈ `{tier, agent}`; `scope_key` = tier name (`reasoning`/`balanced`/`fast`) or agent name. (Utility completions resolve straight to a `tier` row — no per-utility override row today; a `utility` scope_type can be added later if a utility role ever needs to diverge from its tier.)
- `provider`, `model_id`, `effort` (neutral: `none`/`low`/`medium`/`high`), `max_tokens`, `temperature`, `params` JSONB, `enabled`, timestamps.

Both tables follow Muldro conventions: `workspace_id` FK, ULID-prefixed IDs, JSONB for flexible fields, typed columns for indexed fields.

### 4.3 Credential encryption

Envelope encryption with a master key from env/secrets-manager (`MULDRO_CONFIG_ENCRYPTION_KEY`, AES-GCM or Fernet). Each `api_key` is encrypted before persistence and decrypted **only** inside the resolver at model-build time. Plaintext keys are **never logged** and **never returned** over the API (write-only). Missing master key at startup is a fail-loud config error when any non-env credential exists.

---

## 5. Resolver + Capability Map

### 5.1 `ModelResolver.resolve(*, tier=None, agent=None, workspace_id=None) -> ResolvedModel`

The caller states what it is resolving:
- **Agent build** passes `agent=<name>`. The resolver reads that agent's tier (`agents.model_tier`) as the fallback tier.
- **Utility completion** passes `tier=<name>` directly (as `build_utility_model` does today) — utility roles have no override row; they resolve straight to the tier binding.

1. **Determine the fallback tier** — for an agent call, from `agents.model_tier`; for a utility call, the passed `tier`.
2. **Binding lookup (precedence):** for an agent call, an `agent` override row for that name → else the `tier` row for its tier; for a utility call, the `tier` row directly. In all cases, a missing workspace row falls through to the deployment-default (`workspace_id IS NULL`) row.
3. **Catalog lookup:** `MODEL_CATALOG[provider][model_id]` → `ModelSpec` (capabilities + cost).
4. **Credential lookup + decrypt:** workspace `(workspace_id, provider)` row → else deployment-default row → else env fallback (`MULDRO_ANTHROPIC_API_KEY` is still a valid anthropic credential source).
5. **Capability mapping** (see 5.2) → provider-specific kwargs.
6. Return `ResolvedModel { provider, model_id, api_key, base_url, kwargs }`.

**`workspace_id` is optional.** `None` resolves against the deployment-default rows. This lets the many utility callers that lack a workspace today keep working without threading `workspace_id` through all of them now (the pragmatic path from Fork 2). Per-workspace resolution lands later by passing the id.

### 5.2 Capability mapping (replaces `_thinking.py`)

Translates the **neutral** inputs (`effort`, `max_tokens`, `temperature`) into each model's real kwargs, keyed by `ModelSpec.thinking_style`:

| `thinking_style` | Effort handling | Temperature |
|------------------|-----------------|-------------|
| `anthropic_adaptive` (Opus 4.7/4.8, Fable/Mythos 5) | `thinking={"type":"adaptive","display":"summarized"}` + `effort=<level>` | dropped |
| `anthropic_legacy` (Sonnet/Haiku legacy) | `thinking={"type":"enabled","budget_tokens":…}` (clamped `< max_tokens`) + `temperature=1` | forced to 1 when thinking on |
| `openai_effort` | `reasoning_effort=<level>` | per `accepts_temperature` |
| `gemini` | provider thinking config | per `accepts_temperature` |
| `none` | omitted | per `accepts_temperature` |

**Fail-safe by construction:** if a binding sets `effort` on a non-thinking model, or `temperature` on a model where `accepts_temperature=False`, the map **drops the offending kwarg** rather than letting the provider reject the request. This eliminates the known 400 class (e.g. temperature + thinking on adaptive Opus).

---

## 6. Model Build Leaf + Integration

- **`src/llm/model_factory.py::build_langchain_model(resolved) -> BaseChatModel`** — now `init_chat_model(resolved.model_id, model_provider=resolved.provider, **resolved.kwargs)`. Single leaf, provider-dispatched. Returns a LangChain `BaseChatModel`, so the deep-runtime / LangGraph / deepagents stack is unchanged.
- **`src/deep_runtime/model_factory.py::build_chat_model(agent, workspace_id=None)`** — `resolve(agent-role/tier, workspace_id) → build_langchain_model`. `_thinking.py` is **deleted**; its logic moves into the catalog-driven capability map.
- **`src/llm/model_factory.py::build_utility_model(tier, workspace_id=None, ...)`** — same shape, now resolver-backed. `complete_text` / `complete_text_with_usage` gain an optional `workspace_id` passthrough (defaulting to `None` → deployment default).

---

## 7. API (`/v1/` prefixed; workspace via `get_current_workspace_id()`)

| Method + Path | Purpose |
|---------------|---------|
| `GET /v1/settings/model-config` | Tier bindings + per-agent overrides + configured providers (**keys masked / never returned**). |
| `PUT /v1/settings/model-config` | Update tier bindings + overrides. |
| `PUT /v1/settings/providers/{provider}/credentials` | Set/replace a provider key (encrypted), optional `base_url`/`extra_config`. |
| `POST /v1/settings/providers/{provider}/test` | Cheap validation call; sets `status`. |
| `DELETE /v1/settings/providers/{provider}/credentials` | Remove a provider credential. |
| `GET /v1/settings/model-catalog` | Providers / models / capabilities for the UI dropdowns. |

All responses use Pydantic models. API keys are **write-only** — never returned, even masked-with-value; the config endpoint returns only a boolean "configured" + `status`.

---

## 8. Frontend — new `model-tab.tsx` in the Settings modal

Sits alongside `trust-tab` / `policy-tab` in `settings-modal.tsx`; reuses the existing modal + Zustand store patterns.

- **Providers section** — per provider: write-only key input (shows "Configured ✓" once set), `base_url` for local/custom endpoints, a **Test** button (drives the `test` endpoint + `status` badge), enable toggle.
- **Tiers section** — three rows (`reasoning` / `balanced` / `fast`), each: provider dropdown (only **configured** providers), model dropdown (catalog-filtered for that provider), effort selector (rendered only if the model supports thinking), `max_tokens`, `temperature` (rendered only if `accepts_temperature`).
- **Advanced (collapsed)** — per-agent overrides, **enumerated from the registry/role-set** (topology-agnostic); each row can override its tier default with a specific model.

Frontend rules from the project standards apply: hooks unconditional at top, no side effects in render, lazy state init for values read from storage, router navigation via `useRouter().replace()` in effects.

---

## 9. Cutover + Migration (full replace, no dual-path)

1. **Alembic migration** creates `provider_credentials` + `model_bindings`.
2. **Behavior-preserving seed** — seed deployment-default (`workspace_id IS NULL`) bindings that reproduce today exactly:
   - `reasoning → claude-opus-4-8`, `balanced → claude-sonnet-4-6`, `fast → claude-haiku-4-5-*`, provider `anthropic`, effort/params matching the current per-agent thinking budgets.
   - Anthropic credential resolved from the existing env `MULDRO_ANTHROPIC_API_KEY`.
   The cutover ships with **identical behavior**; only the resolution path changed.
3. **Tier rename** `opus/sonnet/haiku → reasoning/balanced/fast`:
   - Data migration on `agents.model_tier`.
   - `cheap_mode` (opus→sonnet + halved thinking) re-expressed in the new tier terms (`reasoning → balanced` downgrade).
   - Seed functions (`AGENT_MODEL_TIERS`, `AgentRegistry.seed_defaults`) updated to the new tier names.
4. **Delete** `MODEL_TIERS`, `MODEL_TIER_IDS`, and `_thinking.py`'s Anthropic-only branch — replaced by `model_catalog.py` + `ModelResolver`. The env `MULDRO_ANTHROPIC_API_KEY` survives **only** as a recognized credential source, not a separate code path.

No feature flag, no co-existing legacy path: the resolver **is** the path, seeded to reproduce current behavior. (Consistent with the project's pre-launch "design the clean end state, replace-and-delete" principle.)

---

## 10. Error Handling & Validation

- **Resolver fails closed with a legible error.** Unknown model / missing-or-invalid credential / decryption failure → typed `ModelConfigError` surfaced to the user (e.g. "OpenAI is not configured for this workspace"), **not** a silent swap to a different model. The always-valid seeded anthropic default guarantees the out-of-box path never errors.
- **Override degradation.** A per-agent *override* with a missing credential falls back to the tier default with a logged warning — an override should not break the turn.
- **Key validation on save.** The `test` call marks `status=invalid` and warns; it does not discard the entered key.
- **Capability guard.** The capability map drops provider-incompatible kwargs (prevents the temperature+thinking 400 class by construction).
- **Secrets hygiene.** Plaintext keys never logged, never returned; validation calls use minimal tokens; master encryption key required at startup when non-env credentials exist.

---

## 11. Ripples & Risks (beyond the happy path — in scope)

- **Prompt caching is Anthropic-specific.** The deep-runtime stamps `cache_control` on tools/prompts. For non-Anthropic providers this must be **gated by `ModelSpec.supports_prompt_cache`** (strip/skip the markers) or the call errors/no-ops. Real integration point.
- **Streaming.** `stream_adapter.py` consumes LangChain stream events (provider-neutral in principle) but was written against Anthropic frames. Verify per provider; covered by the test matrix.
- **Budget / cost.** `BudgetTracker` is USD-based; multi-provider cost comes from the catalog's per-model cost fields rather than a single Anthropic rate. Wire the catalog cost into `record_from_span`.
- **Provider SDK dependencies.** `langchain-openai`, `langchain-google-genai`, `langchain-ollama`, etc. **Implementation deviation (accepted):** these ship in `[project.dependencies]` (always-installed) rather than as optional extras. They are thin, pure-Python LangChain wrappers, and always-installing them keeps the resolver + build leaf branch-free (no conditional-import guards) — the small dependency-footprint cost is judged worth the simpler code. The startup preflight still warns if a *configured* provider's package is somehow absent (see §11 preflight / `runtime_preflight`).
- **Tool-calling reliability variance.** Local/open models call tools less reliably than frontier models; out of scope to fix here, but the catalog can flag `tool_calling: strong|weak` for future routing guidance.

---

## 12. Testing Strategy

- **Characterization test (primary safety net).** Seeded defaults build **byte-identical** kwargs to today's Anthropic path (per tier/agent) — proves the cutover is behavior-preserving before anything else changes.
- **Unit** — capability map per `thinking_style`; resolver precedence (tier default / agent override / deployment fallback / missing credential); encryption round-trip; tier-rename data migration.
- **Integration** — API routes against a real DB (key masking, workspace scoping, `test` validation, write-only keys).
- **Provider matrix** — mocked per-provider param-mapping tests asserting `init_chat_model` receives the right kwargs; live provider calls gated (no keys in CI).
- **Frontend** — tab render, dropdown gating by configured providers, write-only key field, effort/temperature fields conditional on capability.

Test harness note: this repo uses a custom `pytest_pyfunc_call` `asyncio.run` hook (no `pytest-asyncio`); real-DB tests self-check reachability + seed the `User → Workspace` FK chain. Full gate: `uv run pytest tests/ --ignore=tests/e2e`.

---

## 13. Open Questions for Implementation Plan

- Exact initial `MODEL_CATALOG` contents (which OpenAI/Gemini/local models to seed vs. leave to the user to add).
- Whether the master encryption key is env-only or integrates the deployment's secrets manager.
- Whether `model-catalog` is fully static or admin-extensible in a later iteration.
