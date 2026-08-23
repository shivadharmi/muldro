# Model & Provider settings — redesign spec

Status: **approved in principle 2026-08-21, not implemented**
Design canvas: https://claude.ai/code/artifact/f0133c7a-7d01-4c72-adfd-51913b5248b1
Scope: `frontend/src/components/settings/`, `backend/src/api/routes_model_config.py`,
`backend/src/contracts/model_config.py`, `backend/src/services/model_config_service.py`,
`backend/src/config/model_catalog.py`

---

## 1. Why

Settings holds six tabs. Five of them are two or three controls. The sixth — Model —
is a matrix: N providers × credentials, 3 tiers × 6 attributes, N agent overrides. It
was built as flat `flex-wrap` rows inside a fixed-width dialog, and it does not fit.

This is arithmetic, not taste. The modal is `max-w-3xl` (768px) with a `sm:w-44`
(176px) rail and `p-5` padding — **552px of usable width**. A binding row needs ~730px
and a credential row ~576px. Both wrap on every viewport that has ever rendered them.
`max_tokens` and `temperature` carry only `aria-label`, so what wraps onto the second
line is two unlabelled number boxes.

Fixing the layout surfaced a second, larger problem: the tab conflates two different
things with different lifecycles, different risk, and — as of the decision below — very
different scale.

## 2. Decisions

Six forks were settled during design. They are not re-opened by implementation.

### 2.1 Providers and Tiers become separate tabs

**Decided: split.** The deciding input is roadmap scale — the provider roster is
expected to reach 15+ within a year (OpenRouter, Bedrock, Azure, Groq, Together,
Fireworks, DeepSeek, Mistral, …). At four providers a credential list is a 200px strip
and sharing a tab is free; at fifteen it is a browsable list that needs search,
filtering and status grouping, and it earns its own surface.

Consequences that follow and are **not** optional:

- `SettingsTab` gains `"providers"`. The rail carries seven items.
- The prerequisite (a tier cannot use an unconfigured provider) now crosses a tab
  boundary. §4.4 specifies exactly what the UI does about that.

### 2.2 Provider credentials do NOT move to `/integrations`

`/integrations` brokers OAuth installs into the founder's own accounts. Those grant
Muldro authority to act as the user, mint capabilities (`email.send`), and are what
`trust_gate` and `permission_gate` govern. Provider credentials are API keys to
inference vendors: they grant no access to the founder's world and mint no capability.

Putting an Anthropic key beside a Gmail install would imply Anthropic is a place Muldro
acts on your behalf. The Providers tab states the distinction in its subtitle.

### 2.3 Tier binding becomes model-first, provider-derived

Two chained `<select>`s are the wrong shape at fifteen providers. Nobody thinks "I want
an OpenRouter model"; they think "I want Kimi K2" or "the cheapest thing with 1M
context". The binding is chosen through **one search across every connected provider**,
with the deciding facts on the row: context window, price per Mtok, thinking style.
Provider becomes derived metadata, displayed but not separately selected.

This also removes defect **F1** structurally: there is no longer a way to select a
provider without simultaneously selecting a model.

### 2.4 A tier can never be *bound* to an unconfigured provider; a *revoked* one warns

Two distinct paths reach "this tier points at a provider with no credential", and they
need different guards. Save-time validation cannot see the second at all.

| Path | Guard |
|------|-------|
| **Bind** — saving a binding for a provider that was never connected | `PUT /v1/model-config` **rejects** with 422, naming the offending `scope_key` and provider |
| **Revoke** — the binding was valid when saved; the credential is later deleted, or a deployment env key disappears | `GET` returns a persistent server-computed `warnings` entry (§4.4) |
| **Seed** — a deployment-default binding created at startup, which never passes through `put_config` | same `warnings` entry |

Both guards call `ModelResolver.resolve_credential` — the same call the run-time path
makes — so validation and reality cannot drift.

`DELETE /v1/providers/{provider}/credentials` additionally returns the bindings that
depend on that provider, so the UI can state the consequence before proceeding. It
**informs, it does not block**: a credential the founder cannot revoke is a security
problem, not a safety feature.

The earlier objection to rejecting at bind time — "the founder may be about to connect
it" — is answered by §4.4 putting a **Connect** action inside the tier card. The
connect-then-bind order costs one click and never leaves the tab.

### 2.5 There is no tier-level fallback

A tier binding whose provider is unusable raises `ModelConfigError`. It does not degrade
to anything. This is a decision, not an omission — `model_resolver.py:70` already states
it: *"A genuinely broken tier config still raises."*

A defensible fallback does exist: `_pick_binding` prefers the workspace row over the
NULL-workspace deployment-default row, choosing by **existence** rather than usability,
and could be extended to fall through when the credential does not resolve. It is
rejected for three reasons.

1. Once §2.4 rejects bad binds, the only remaining route into the broken state is an
   explicit revoke, performed on a screen that just named what it would break. Falling
   back there means the founder is warned, proceeds, and then sees nothing happen —
   while Planner silently runs on a model they did not choose, at a different price,
   producing different plans. A loud failure they consented to is better.
2. `_effective_binding` exists so cache identity and cost attribution report the model
   *actually running* (see its docstring). Any new fallback must be mirrored there or
   prompt-cache gating and per-call cost attribution both go wrong.
3. The articulacy problem is real but separate, and is fixed by **B7** rather than by
   degradation.

**Revisit trigger:** multi-user workspaces. The argument above rests on the revoker and
the binding's owner being the same person. Once admin A can revoke a credential admin B's
tier depends on, the revoke is no longer consented by the affected party, and
degradation-with-a-visible-signal becomes the better answer.

### 2.6 The binding contract is realigned to its storage model

`TierBinding` is replaced by `ModelBindingDTO` carrying `scope_type` + `scope_key`, the
pair the DB already stores.

Today the contract flattens both into a single `tier` field and recovers `scope_type`
*positionally* — from which array of `ModelConfigResponse` the binding was found in.
That is a lossy projection of its own storage model, and it is why `_to_tier_binding`
takes a `tier_binding_cls` parameter, why `put_config` must comment that "the reused
TierBinding carries the agent name in the `tier` field", and why the field carries a
two-line apology in `contracts/model_config.py`.

The rename is folded into Phase 1 because §4.6 is **already** a breaking contract change
— `CatalogResponse.providers` goes from `dict` to `list`, models flatten, `warnings`
appears, `effort` becomes a `Literal`. Every consumer is rewritten in that phase
regardless, so aligning the binding shape costs a diff rather than a second breaking
change. Pre-launch there is nothing to migrate.

---

## 3. Defects to fix

All twenty-one are verified against source at the cited locations. Severity: **S1** =
data loss or run-time failure, **S2** = user-visible incorrectness, **S3** = quality.

### 3.1 Backend / data integrity

| ID | Sev | Defect |
|----|-----|--------|
| **B1** | S1 | **Saving a key wipes `base_url` and `extra_config`.** `put_provider_credential` assigns `existing.base_url = body.base_url` and `existing.extra_config = body.extra_config` unconditionally. `api.ts:1097` sends `base_url: undefined` (dropped by `JSON.stringify`) and never sends `extra_config`. Rotating an Anthropic key clears its custom base URL; saving Ollama without retyping its URL unconfigures it, because `base_url` is its only credential. |
| **B2** | S1 | **`ProviderStatus` never exposes `base_url`.** The GET returns `provider/configured/status/source` only, so the UI cannot display the URL in effect nor round-trip it. With B1 this makes editing any configured provider destructive-by-default. |
| **B3** | S1 | **A tier may be bound to an unconfigured provider, and it hard-fails at run time.** `put_config` validates only that `get_model_spec(provider, model_id)` resolves. `ModelResolver.resolve` then raises `ModelConfigError("provider X is not configured")` (`model_resolver.py:95`). The degradation path at `model_resolver.py:178` covers **agent overrides only** — it falls back to the agent's tier row. A *tier* row has nothing to fall back to. |
| **B4** | S2 | **`effort: "none"` is a legal contract value the UI cannot render.** `TierBinding.effort: str = "none"`; `EFFORT_OPTIONS = ["low","medium","high"]`. `addOverride` seeds `"none"` when no tier binding is found, so the `<select>` shows `low` while state holds `none`, and Save persists `none`. `effort` is an unvalidated `str`. |
| **B5** | S2 | **Catalog metadata is dropped at the API boundary.** `ModelSpec` carries `context_window`, `input_cost_per_1k`, `output_cost_per_1k`, `supports_prompt_cache`; `CatalogModel` exposes none. There is no provider-level object at all (`providers: dict[str, list[CatalogModel]]`), so the UI has no display name, auth shape, or model count — it renders raw slugs like `google_genai`. |
| **B6** | S3 | `_provider_statuses` iterates `for provider in MODEL_CATALOG`, so a credential row for a provider absent from the catalog is invisible and unmanageable. |
| **B7** | S2 | **`ModelConfigError` is caught nowhere.** It is raised at `model_resolver.py:66`, `:92` and `:96`; the only `except` in `src/` is the resolver's own internal one at `:70`. It subclasses `RuntimeError`, so a misconfigured tier surfaces as an unhandled exception at agent-build time — an error frame in chat, a `failed` step in a DAG run — and nothing names the tier, the provider, or the fix. This is the failure mode §2.5 deliberately leaves in place, so it must be made articulate. |

### 3.2 Frontend correctness

| ID | Sev | Defect |
|----|-----|--------|
| **F1** | S2 | **Changing provider blanks the model, and Save then 400s.** `model-tab.tsx:96` sets `model_id: ""`; nothing gates Save; `put_config` rejects with `unknown model <provider>/`. |
| **F2** | S2 | **Two Save buttons, identical behaviour.** "Save" and "Save overrides" both call `handleSave()`, which posts tiers *and* overrides. Either button saves both sections. |
| **F3** | S2 | **No dirty tracking.** Save is always enabled, nothing indicates a pending edit, and closing the modal discards silently. |
| **F4** | S2 | **Conditional controls reflow the row.** `showEffort` / `showTemperature` unmount their controls when the model doesn't support them, so switching model changes the control count and shifts everything after it. |
| **F5** | S3 | **The API key stays in component state after saving.** `ProviderRow` never clears `apiKey`, so the secret lives in React state and in the DOM input for the modal's lifetime. |
| **F6** | S3 | **The binding contract is a lossy projection of its storage model.** `TierBinding.tier` carries a tier name *or* an agent name; `scope_type` is recovered positionally from which response array the binding sits in. This is **not** a collision bug — `updateTier` and `updateOverride` map over separate arrays, so an agent named `reasoning` cannot clash, and no agent in `AGENT_MODEL_TIERS` is named after a tier. The cost is clarity: a `tier_binding_cls` parameter on `_to_tier_binding`, an explanatory comment in `put_config`, and a two-line apology in the contract. Resolved by §2.6. |

### 3.3 Layout and responsiveness

| ID | Sev | Defect |
|----|-----|--------|
| **L1** | S2 | Binding row needs ~730px in 552px; credential row needs ~576px in 512px. Both wrap on every viewport. |
| **L2** | S2 | `max_tokens` and `temperature` have only `aria-label`, so after wrapping they are anonymous number boxes. |
| **L3** | S2 | `h-[600px]` is fixed — never grows on a tall display, clips on a short one. |
| **L4** | S2 | The mobile rail is a horizontal scroller for six (soon seven) tabs, with no scroll affordance, inside that fixed box. |
| **L5** | S3 | `settings-modal.tsx` is 529 lines and owns every tab's state — over the 400-line component cap in `docs/engineering-standards.md`. |

### 3.4 Accessibility

| ID | Sev | Defect |
|----|-----|--------|
| **A1** | S2 | No focus trap and no initial focus. `role="dialog" aria-modal="true"` is set, but Tab escapes to the page behind and focus is not restored on close. |
| **A2** | S3 | The dialog uses `aria-label="Settings"` while a visible `<h2>Settings</h2>` exists; they are not wired via `aria-labelledby`. |
| **A3** | S3 | Native `<select>` options carry raw slugs, so a screen reader announces `google_genai`. |

---

## 4. Target design

This section specifies **behaviour and structure**. Every pixel value — tokens, type
scale, control metrics, per-component anatomy, icon geometry — is in **§9 Visual
specification**, which is the authoritative source and is transcribed directly from the
canvas artboards. Where §4 names a component, §9 has its metric table.


### 4.1 Modal shell

One shell, two behaviours, one breakpoint at `sm` (640px).

**At `sm` and above** — dialog:

```
w-full max-w-4xl mx-4
h-[min(820px,calc(100dvh-4rem))]
```

`dvh` rather than `vh`, so mobile browser chrome does not clip the footer. Every flex
child in the height chain carries `min-h-0` so the scroll region actually scrolls
instead of overflowing its parent. This fixes **L3**.

**Below `sm`** — full sheet: `inset-0`, no rounding, no backdrop margin. The rail is
not a scroller; it is the sheet's **root view**, a list of the seven tabs. Selecting one
pushes that tab full-screen with a `‹ Settings` back control. This fixes **L4** and
removes the horizontal-scroll affordance problem entirely rather than decorating it.

Accessibility (**A1**, **A2**): focus moves to the dialog on open, is trapped while
open, and is restored to the invoking control on close. `aria-labelledby` points at the
visible heading. Esc continues to close.

### 4.2 Decomposition

`settings-modal.tsx` becomes a shell — frame, rail, focus management, routing to a tab.
It owns no tab's data. This fixes **L5**.

```
src/components/settings/
  settings-modal.tsx            shell only
  settings-rail.tsx             tab list; the mobile root view
  tabs/
    model-tab.tsx               tiers + agent overrides
    providers-tab.tsx           NEW
    (account|preferences|policy|trust|spending)-tab.tsx   unchanged
  model/
    tier-card.tsx
    binding-fields.tsx          the labelled grid
    model-picker.tsx            command palette
    agent-overrides.tsx
  providers/
    provider-row.tsx
    provider-credential-form.tsx    schema-driven, see §4.5
    provider-filter.tsx
  hooks/
    use-model-config.ts         load, draft, dirty, save
    use-provider-credentials.ts save, test, delete
```

Each file stays under the 400-line component cap.

### 4.3 Model tab — tiers only

Three tier cards. Each card:

- **Header** — tier name, one-line purpose, chips naming the agents on that tier
  (from `AGENT_MODEL_TIERS`, served by `CatalogResponse.agents`).
- **Fields**, a labelled CSS grid that cannot wrap into anonymity (fixes **L1**, **L2**):

  | | Model | Effort | Max tokens | Temperature |
  |---|---|---|---|---|
  | `sm`+ | `2.7fr` | `1fr` | `1.1fr` | `1.1fr` |
  | below `sm` | span 2 | `1fr` | `1fr` | span 2 |

  Control height 36px at `sm`+, **44px below** (touch target minimum).
- **Meta line** — context window, `$in / $out` per Mtok, thinking style. Sourced from
  the catalog fields added in §4.6.

**Unsupported controls are disabled, never unmounted** (fixes **F4**). When
`accepts_temperature` is false the field renders disabled reading *"Not accepted"* with
the reason in the meta line. When `thinking_style === "none"` the effort field renders
disabled reading *"n/a"* — which is also how `effort: "none"` becomes representable
(fixes **B4**).

There is **one** Save affordance: a sticky footer bar carrying the dirty count, a
Discard, and a Save (fixes **F2**, **F3**). Save is disabled when clean. Dirty state is
`draft !== saved` by structural comparison; the count is the number of differing
bindings.

Per-agent overrides remain a collapsed disclosure with an active count, using the same
`binding-fields` grid when expanded.

### 4.4 The unconfigured-provider state

Per §2.4, §2.5 and **B3**. Three mechanisms, all deriving from one call —
`ModelResolver.resolve_credential`, the same one the run-time path makes.

**Bind is rejected.** `PUT /v1/model-config` validates, per binding, that the provider
resolves a credential (or is in `KEYLESS_PROVIDERS`). A failure is a 422 naming the
`scope_key` and provider. The client surfaces it on the offending tier card, not as a
toast, so the founder sees which binding was refused.

**Revoke is warned.** Computed server-side so it survives a reload:

```python
class ConfigWarning(BaseModel):
    scope_type: Literal["tier", "agent"]
    scope_key: str
    code: Literal["provider_not_configured"]
    message: str
```

`ModelConfigResponse` gains `warnings: list[ConfigWarning]`, populated on **both** GET
and PUT. This is not a softer version of the reject — it is the guard for the two paths
a save-time check cannot see: a credential deleted after the binding was saved, and a
deployment-default binding seeded at startup that never passed through `put_config`.

A warned tier card renders with an amber border and a footer row stating the real
consequence — *"Groq is not connected. There is no tier fallback — every agent on Fast
will fail until you connect it."* — plus a **Connect Groq** action that switches to the
Providers tab with that provider pre-expanded. The copy must not promise a fallback;
per §2.5 there is none.

**Revoke is informed before it happens.** `DELETE /v1/providers/{provider}/credentials`
returns the dependent bindings alongside the resulting `ProviderStatus`:

```python
class CredentialDeleteResponse(BaseModel):
    status: ProviderStatus
    orphaned_bindings: list[ConfigWarning]
```

The UI confirms first — *"Removing Groq breaks the Fast tier"* — then proceeds. It never
blocks: a credential the founder cannot revoke is a security problem.

**The remaining failure is made articulate (B7).** `ModelConfigError` is caught at the
agent-build boundary in `deep_runtime/model_factory.py` and re-raised as a typed error
carrying `scope_type`, `scope_key`, `provider` and a remediation string. Chat renders it
as an actionable message naming the tier; an autonomous step records it on the
`TaskStep` before transitioning to `failed`, so a broken tier is diagnosable from the run
rather than only from the logs.

### 4.5 Providers tab

A searchable list, because fifteen rows is a list and four was a strip.

- **Subtitle** carries §2.2's distinction and links to `/integrations`.
- **Toolbar** — search across provider name and model names; a segmented
  `All / Connected / Available` filter.
- **Grouping** — `Connected` then `Available`, each with a count.
- **Row** — status dot, display name, auth-kind chip, credential status chip, source
  ("workspace key" / "from environment" / "deployment default"), and actions
  (`Test`, `Edit` / `Connect`; `Remove` only when `source === "workspace"`, preserving
  today's correct behaviour).
- **Expanded row** — the credential form, inline. Expansion is exclusive (one at a time).

**The credential form is schema-driven, not a fixed `(api_key, base_url)` pair.** Bedrock
needs region + access key + secret; Azure needs endpoint + deployment + api-version;
Ollama needs a base URL and no key. The fields come from the catalog (§4.6) and land in
`extra_config`, which already exists on `CredentialBody` and `ProviderCredential`.

The form is pre-filled with the **non-secret** values currently in effect — `base_url`
and `extra_config_public` (§4.6) — which **B2** requires. Secret fields render empty with
a "configured — leave blank to keep" hint when `extra_config_secret_keys` names them, and
are submitted only when retyped. On successful save the local secret state is cleared
(fixes **F5**).

### 4.6 Backend contract

`model_catalog.py` gains provider-level facts alongside the existing per-model ones:

```python
@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    display_name: str                 # "Google Gemini", never "google_genai"
    auth_kind: Literal["api_key", "keyless_base_url", "aws_sigv4", "azure_deployment"]
    credential_fields: tuple[CredentialField, ...]
    docs_url: str | None

@dataclass(frozen=True)
class CredentialField:
    key: str                          # "api_key" | "base_url" | "region" | ...
    label: str
    kind: Literal["secret", "text", "url"]
    required: bool
    placeholder: str | None
```

`routes_model_config.py`:

```python
class CatalogModel(BaseModel):
    provider: str                     # NEW — models are now flat
    model_id: str
    display_name: str
    thinking_style: str
    accepts_temperature: bool
    suggested_tier: str
    context_window: int               # NEW
    input_cost_per_1k: float          # NEW
    output_cost_per_1k: float         # NEW
    supports_prompt_cache: bool       # NEW

class CatalogProvider(BaseModel):     # NEW — the API projection of ProviderSpec
    provider: str
    display_name: str
    auth_kind: Literal["api_key", "keyless_base_url", "aws_sigv4", "azure_deployment"]
    credential_fields: list[CredentialFieldModel]
    model_count: int
    docs_url: str | None

class CredentialFieldModel(BaseModel):
    key: str
    label: str
    kind: Literal["secret", "text", "url"]
    required: bool
    placeholder: str | None

class CatalogResponse(BaseModel):
    providers: list[CatalogProvider]  # WAS dict[str, list[CatalogModel]]
    models: list[CatalogModel]        # NEW — flat, for cross-provider search
    agents: list[AgentInfo]
```

Flattening `models` is what makes §2.3's single search a filter rather than a nested
walk. Pre-launch, with no data to migrate, this replaces the nested shape outright — no
dual path (per `docs/engineering-standards.md` and the no-migration-hedges rule).

`contracts/model_config.py`:

```python
class ProviderStatus(BaseModel):
    provider: str
    configured: bool
    status: str
    source: Literal["workspace", "default", "env", "none"]
    base_url: str | None = None              # NEW — fixes B2
    extra_config_public: dict[str, str] = {} # NEW — values for NON-secret fields
    extra_config_secret_keys: list[str] = [] # NEW — names only, for secret fields

class ModelBindingDTO(BaseModel):        # WAS TierBinding — §2.6, F6
    scope_type: Literal["tier", "agent"] # NEW — was recovered positionally
    scope_key: str                       # WAS `tier`, which carried both meanings
    provider: str
    model_id: str
    effort: Literal["none", "low", "medium", "high"] = "none"   # was bare str — B4
    max_tokens: int = Field(4096, ge=1)
    temperature: float | None = None
```

`ModelConfigResponse.tiers` / `.agent_overrides` keep their split (the UI renders two
sections), but each entry now states its own scope rather than depending on which list it
arrived in. `_to_tier_binding` loses its `tier_binding_cls` parameter, `put_config` loses
its explanatory comment, and the contract loses its apology.

The extra-config split is what makes the schema-driven form round-trippable without
leaking. `CredentialField.kind` classifies each field: values for `text`/`url` fields
(an AWS region, an Azure deployment name) come back in `extra_config_public` so the form
can pre-fill them; `secret` fields return their **name only** in
`extra_config_secret_keys`, so the form can render "configured — leave blank to keep"
without ever echoing the value. The response envelope stays write-only for secrets.

**B1 fix** — `put_provider_credential` becomes a genuine partial update:

```python
fields = body.model_dump(exclude_unset=True)
if "api_key" in fields:
    existing.api_key_encrypted = secret_crypto.encrypt_secret(fields["api_key"])
if "base_url" in fields:
    existing.base_url = fields["base_url"]
if "extra_config" in fields:
    existing.extra_config = fields["extra_config"]
```

An omitted field is left alone; an explicit `null` clears it. The client sends the full
field set from the form regardless, so the two agree, but the server no longer destroys
state on a partial body from any caller.

**B6 fix** — `_provider_statuses` iterates the union of catalogued providers and
providers with credential rows, so an orphaned row is visible and removable.

### 4.7 Model picker

A command palette, not a dropdown — at fifteen providers a 261px popover cannot carry
the metadata the decision needs.

- Opens from the Model field; `Esc` closes; `↑ ↓` navigate; `↵` selects.
- Search matches model display name, provider display name, and numeric facts.
- Sections: **Suggested for `<tier>`** (from `suggested_tier`), then one section per
  connected provider.
- Row: name, thinking-style chip, context window, `$in / $out`, provider.
- Footer: count of unconnected providers and a **Browse all providers** action. They are
  surfaced, not filtered out of existence — today `configuredProviders` hides them, which
  is why the prerequisite is invisible until it fails.
- Combobox semantics: `role="listbox"`, `aria-activedescendant`, display names only
  (fixes **A3**).

---

## 5. Testing

Backend tests are written **unmarked** — `conftest.py`'s `pytest_pyfunc_call` hook runs
unmarked coroutines, and CLAUDE.md's `asyncio_mode = "auto"` note is stale.

**Backend**

- `put_provider_credential` with `{"api_key": "..."}` alone **preserves** the stored
  `base_url` and `extra_config` (B1 regression guard — the highest-value test here).
- Explicit `{"base_url": null}` clears it.
- Ollama saved with only `base_url` is `configured`; a later key-only save does not
  unconfigure it.
- `GET /v1/model-config` returns `base_url` and non-secret `extra_config_public` values,
  and **never** the value of a field whose `CredentialField.kind == "secret"`.
- `PUT /v1/model-config` **rejects** with 422 a binding whose provider resolves no
  credential, and the detail names the `scope_key` and the provider (§2.4, bind path).
- A keyless provider (`ollama`) with only a `base_url` is **accepted** by that same
  validation — `KEYLESS_PROVIDERS` must be honoured, or the reject breaks local models.
- `warnings` is populated for a tier whose credential was deleted **after** the binding
  was saved, on both GET and PUT, and agrees with `ModelResolver.resolve_credential`
  (§2.4, revoke path — the case save-time validation cannot reach).
- A deployment-default binding seeded with no matching credential produces a warning
  without ever passing through `put_config` (§2.4, seed path).
- `DELETE /v1/providers/{p}/credentials` returns `orphaned_bindings` naming every tier
  and agent override that depended on it, and still deletes.
- `ModelConfigError` from a broken tier is caught at the agent-build boundary and
  re-raised carrying `scope_type`, `scope_key` and `provider` — never as a bare
  `RuntimeError` (B7).
- A tier binding whose provider is unusable **raises**; it does not silently resolve to
  the deployment-default row (§2.5 — a regression guard against re-adding a fallback).
- `effort` outside the Literal is rejected with 422.
- `ModelBindingDTO` round-trips `scope_type`, and an agent override named after a tier
  (`scope_type="agent", scope_key="reasoning"`) is stored and returned distinctly from
  the tier of the same name (§2.6).
- `CatalogResponse.models` is flat, carries `provider`, and exposes the four new fields.
- A credential row for a non-catalogued provider appears in `provider_statuses` (B6).

**Frontend**

- Save is disabled when clean and enabled after any field edit; the count matches the
  number of changed bindings (F3).
- One Save affordance persists both tiers and overrides; the second button is gone (F2).
- A model with `accepts_temperature: false` renders the temperature control **disabled
  and present**, not removed (F4).
- A model with `thinking_style: "none"` renders effort disabled as `n/a` (B4).
- The picker filters across providers and its footer counts unconnected ones.
- Saving a provider key clears the secret from state (F5).
- The credential form pre-fills `base_url` from `ProviderStatus` and re-submits it (B1/B2
  at the client boundary).
- Focus is trapped while open and restored on close (A1).
- At 390px the field grid is two columns and controls are ≥44px; at 1024px it is four
  columns and nothing wraps (L1, L2).

Existing `model-tab.test.tsx` and `settings-modal.test.tsx` are rewritten against the new
component boundaries rather than patched.

---

## 6. Phasing

Each phase is independently reviewable and leaves the app working.

1. **Backend contract** — `ProviderSpec`/`CredentialField`, flat `models`, the four
   catalog fields, `base_url` + the `extra_config` split on `ProviderStatus`, the
   `effort` Literal, `ModelBindingDTO` (§2.6), bind-time rejection and `warnings`
   (§2.4), `orphaned_bindings` on DELETE, the partial-update fix (B1), B6, and the
   typed `ModelConfigError` boundary (B7). Tests first.
2. **Shell** — responsive frame, `dvh` height chain, focus trap, mobile push-list,
   decomposition of `settings-modal.tsx` into shell + `use-model-config`.
3. **Providers tab** — new tab, list, search, filter, schema-driven credential form.
4. **Model tab** — tier cards, labelled grid, model picker, single save bar, warning state.
5. **Mobile and a11y pass** — 44px targets, combobox semantics, `aria-labelledby`, and
   the responsive assertions above.

Phase 1 is a prerequisite for 3 and 4. Phases 3 and 4 are independent of each other.

---

## 7. Out of scope

- Moving provider credentials to `/integrations` (rejected, §2.2).
- A full page route at `/settings/model` (explored, superseded by the split — page 2 of
  the canvas).
- Per-model prompt-cache configuration. `supports_prompt_cache` is exposed for display
  only; nothing in this spec changes cache behaviour.
- Adding actual new providers. The spec makes fifteen *representable*; the roster beyond
  the four in `MODEL_CATALOG` is not part of this work.
- **Any change to `ModelResolver` precedence, or a new tier-level fallback.** Settled in
  §2.5, including the one fallback that would not have been invented (extending
  `_pick_binding`'s workspace→default ladder to skip an unusable row) and why it is still
  rejected. `_effective_binding`'s agent-override degradation is unchanged.
- **`AGENT_MODEL_TIERS` and the tier taxonomy.** `reasoning` / `balanced` / `fast` and
  which agent sits on which are code facts this work displays; it does not change them.

---

## 8. Open questions

None. The three calls previously flagged here — the binding-contract rename, the
bind-time policy, and the tier-fallback question — are resolved in §2.6, §2.4 and §2.5
respectively, with their reasoning recorded there rather than left to implementation.

One correction is worth recording, because the first draft of this spec asserted it:
**F6 was described as a key collision, and it is not.** `updateTier` and `updateOverride`
map over separate arrays with separate setters, so an agent named after a tier cannot
clash, and no agent in `AGENT_MODEL_TIERS` is so named. The rename in §2.6 is justified
by the lossy contract projection, not by a bug.

---

## 9. Visual specification

Transcribed from the canvas artboards (`design-canvas/*.dc.html`). These are the values
to implement, not approximations of them. Where an artboard and this section disagree,
this section wins and the artboard is corrected.

### 9.1 Token map — artboards to this codebase

The artboards use raw `hsl()` for legibility. Every one resolves to an existing token in
`frontend/src/app/globals.css`. **Use the Tailwind class; never re-enter a raw colour.**

| Artboard var | Resolved (dark) | Theme token | Tailwind class |
|---|---|---|---|
| `--bg` | `hsl(220 20% 7%)` | `--color-surface-0` | `bg-surface-0` |
| `--s1` | `hsl(220 18% 10%)` | `--color-surface-1` | `bg-surface-1` |
| `--s2` | `hsl(220 16% 13%)` | `--color-surface-2` | `bg-surface-2` |
| `--s3` | `hsl(220 14% 17%)` | `--color-surface-3` | `bg-surface-3` |
| `--s4` | `hsl(220 12% 22%)` | `--color-surface-4` | `bg-surface-4` |
| `--tp` | `hsl(220 20% 95%)` | `--color-t-primary` | `text-t-primary` |
| `--ts` | `hsl(220 12% 72%)` | `--color-t-secondary` | `text-t-secondary` |
| `--tt` | `hsl(220 8% 52%)` | `--color-t-tertiary` | `text-t-tertiary` |
| `--tm` | `hsl(220 6% 40%)` | `--color-t-muted` | `text-t-muted` |
| `--bsub` | `hsl(220 16% 16%)` | `--color-b-secondary` | `border-b-secondary` |
| `--bdef` | `hsl(220 14% 22%)` | `--color-b-primary` | `border-b-primary` |
| `--bstr` | `hsl(220 12% 32%)` | `--color-b-strong` | `border-b-strong` |
| `--pri` | `hsl(193 100% 62%)` | `--color-j-primary` | `text-j-primary` / `bg-j-primary` |
| `--pris` | `j-primary / 12%` | `--color-j-primary-soft` | `bg-j-primary-soft` |
| `--prifg` | `hsl(220 20% 7%)` | `--color-j-primary-fg` | `text-j-primary-fg` |
| `--sec` / `--secs` | violet | `--color-j-secondary(-soft)` | `text-j-secondary` / `bg-j-secondary-soft` |
| `--ok` / `--oks` | `hsl(155 60% 44%)` | `--color-j-success(-soft)` | `text-j-success` / `bg-j-success-soft` |
| `--warn` / `--warns` | `hsl(36 90% 60%)` | `--color-j-warning(-soft)` | `text-j-warning` / `bg-j-warning-soft` |
| `--err` / `--errs` | `hsl(351 90% 68%)` | `--color-j-error(-soft)` | `text-j-error` / `bg-j-error-soft` |

> **Naming trap.** In this codebase `b-secondary` is the **subtle** border and
> `b-primary` is the **default**, stronger one. Every hairline in the existing settings
> code uses `border-b-secondary`, and the artboards' `--bsub` maps to it. Reaching for
> `border-b-primary` because it sounds primary produces visibly heavier rules.

Radii and shadows come from the same file, in the arbitrary-value form the codebase
already uses: `rounded-[var(--radius-md)]` (8px), `-lg` (12px), `-xl` (16px), `-full`;
`shadow-[var(--shadow-lg)]`.

Translucent surfaces:

| Artboard | Tailwind |
|---|---|
| rail background `hsl(220 16% 13% / .4)` | `bg-surface-2/40` |
| save bar / palette footer `.5` / `.6` | `bg-surface-2/50`, `bg-surface-2/60` |
| disabled control fill `hsl(220 16% 13% / .45)` | `bg-surface-2/45` |
| expanded provider row `hsl(193 100% 62% / .05)` | `bg-j-primary/5` |
| warning card border `hsl(36 90% 60% / .35)` | `border-j-warning/35` |
| warning card inner rule `.25` | `border-j-warning/25` |
| warning ghost button border `.4` | `border-j-warning/40` |

### 9.2 Type scale

Geist throughout (already loaded as `--font-geist-sans`). Tabular numerals on every
numeric field, cost, context window and count, so columns align and values do not jitter
while being edited.

| Role | Size | Weight | Colour |
|---|---|---|---|
| Modal title | 15px | 600 | `t-primary` |
| Modal subtitle | 12.5px | 400 | `t-tertiary`, line-height 1.5 |
| Section header (`sec-h`) | 11px | 500 | `t-muted`, uppercase, tracking `.08em` |
| Palette group header | 10px | 500 | `t-muted`, uppercase, tracking `.08em` |
| Tier name | 13px | 600 | `t-primary`, tracking `.02em` |
| Tier description | 12px | 400 | `t-tertiary` |
| Rail item | 13px | 400 / 500 active | `t-tertiary` / `j-primary` active |
| Control value | 14px | 400 | `t-primary` |
| Control label (`ctl-lbl`) | 10px | 500 | `t-muted`, uppercase, tracking `.07em` |
| Provider row name | 14px | 500 | `t-primary` |
| Picker row | 13.5px | 400 / 500 selected | `t-primary` |
| Meta / hint | 11.5px | 400 | `t-muted` |
| Chip | 11px | 500 | per variant |
| Button | 13px | 500 primary / 400 ghost | per variant |

### 9.3 Control primitives

**`ctl` — form control** (select trigger, number input, search field)

| | Desktop (`sm`+) | Mobile |
|---|---|---|
| height | 36px | **44px** |
| font-size | 14px | 15px |
| padding-x | 10px | 12px |
| radius | 8px (`--radius-md`) | same |
| background | `bg-surface-2` | same |
| border | 1px `border-b-secondary` | same |
| layout | `flex items-center justify-between gap-2` | same |

- **Focus:** the existing `:focus-visible` rule in `globals.css` (2px `--muldro-primary`,
  offset 2px). Do not add a second ring.
- **Changed (dirty):** `border-j-primary` plus
  `shadow-[0_0_0_1px_var(--muldro-primary-soft)]`.
- **`ctl-off` — disabled:** `bg-surface-2/45`, **dashed** border, `text-t-muted`, 12px.
  Used for Temperature when `accepts_temperature` is false and for Effort when
  `thinking_style === "none"`. Always rendered, never unmounted (**F4**).
- **`ctl-lbl`:** 10px/500 uppercase `t-muted`, tracking `.07em`, `margin-bottom: 5px`
  (6px mobile). Every control carries a visible label — the fix for **L2**.

**Buttons.** Two heights, chosen by context rather than by emphasis:

| Variant | Height | Padding-x | Where |
|---|---|---|---|
| `md` | 32px | 13px primary / 12px ghost | save bar, card-level actions, warning card |
| `sm` | 30px | 12px primary / 11px ghost | inside dense list rows, palette footer |
| mobile | **44px** | 18px / 16px | every button below `sm` |

Primary `bg-j-primary text-j-primary-fg` 13px/500 radius 8px, hover `bg-j-primary-hover`.
Ghost transparent, `text-t-secondary`, 1px `border-b-primary`, 13px.
Danger ghost (`Remove`) is ghost plus `text-j-error`.

**Chips.** Three defined sizes; do not invent a fourth.

| Name | Height | Padding-x | Radius | Font | Use |
|---|---|---|---|---|---|
| `chip` | 20px | 8px | full | 11px/500 | status, source, counts |
| `agent-chip` | 19px | 7px | 5px | 11px/400 | agents on a tier |
| `tchip` | 18px | 7px | 5px | 10.5px/400 | thinking style, picker rows |

Variants: neutral `bg-surface-3 text-t-tertiary`; success `bg-j-success-soft
text-j-success`; warning `bg-j-warning-soft text-j-warning`; info `bg-j-primary-soft
text-j-primary`; outline `bg-transparent text-t-muted border border-b-primary`.

**`kbd`** (picker only): min-width 17px, height 17px, padding-x 4px, radius 4px,
`bg-surface-3`, 1px `border-b-primary`, `text-t-tertiary`, 10.5px.

**Status dot:** 7px circle — connected `bg-j-success`; degraded `bg-j-warning`; not
connected `border-[1.5px] border-t-muted` on transparent.

### 9.4 Modal shell and rail

**Normalisation.** The artboards render the modal at 780px (Model) and 820px (Providers)
because each was drawn to its own content. The implementation uses **one fixed height**,
so switching tabs never resizes the dialog.

```
sm+   : w-full max-w-4xl (896px) mx-4
        h-[min(820px,calc(100dvh-4rem))]
        bg-surface-1  border border-b-secondary
        rounded-[var(--radius-xl)]  shadow-[var(--shadow-lg)]
below : fixed inset-0 — full sheet, no radius, no margin
```

Backdrop `bg-black/50 backdrop-blur-sm`; entry reuses the existing `animate-fade-in` /
`animate-scale-in`. Every flex child in the height chain carries `min-h-0`.

**Rail** (`sm`+): width **200px**, `border-r border-b-secondary`, `bg-surface-2/40`,
padding 10px, `gap-[2px]`.

| Element | Metric |
|---|---|
| "Settings" heading | padding `8px 10px 12px`, 15px/600 |
| Rail item | `flex items-center gap-[10px]`, padding `7px 12px`, radius 8px, 13px |
| Rail item — inactive | `text-t-tertiary`, hover `text-t-primary bg-surface-2` |
| Rail item — active | `bg-j-primary-soft text-j-primary font-medium` |
| Providers item suffix | right-aligned `4/15`, 11px `t-muted`, tabular-nums |

**Header:** padding `16px 24px`, `border-b border-b-secondary`. Title 15px/600; subtitle
12.5px `t-tertiary` at `margin-top: 3px`. Close icon 16px `t-muted`, hover `t-primary`.

**Body:** padding `20px 24px`, `flex flex-col gap-[18px]`, `overflow-y-auto`.

### 9.5 Tier card

`bg-surface-1`, 1px `border-b-secondary`, radius 12px, padding **`13px 20px 11px`**.
Cards stack with `gap-[10px]`.

**Header row** — `flex items-center justify-between gap-3`, `margin-bottom: 11px`: tier
name (13px/600, tracking `.02em`) and description (12px `t-tertiary`) on a `gap-[10px]`
baseline row; agent chips right at `gap-[5px]`.

**Field grid** — `gap-[12px]`:

| Breakpoint | `grid-template-columns` |
|---|---|
| `sm`+ | `2.7fr 1fr 1.1fr 1.1fr` — Model, Effort, Max tokens, Temperature |
| below `sm` | `1fr 1fr`; Model `col-span-2`; Effort + Max tokens share a row; Temperature `col-span-2` |

At 896px that resolves to Model ≈ 261px, Effort ≈ 97px, Max tokens ≈ 106px, Temperature
≈ 106px. Nothing wraps at any width — the fix for **L1**.

**Model control interior:** display name left, ellipsis on overflow; right cluster
`gap-[7px]` carrying the derived provider name (11.5px `t-muted`) then an 11px search
glyph (`t-tertiary`). A search glyph, not a chevron — it opens the palette (§9.9), not a
dropdown, and the affordance must say so.

**Meta row** — `margin-top: 10px`, `padding-top: 9px`, `border-t border-b-secondary`,
`flex items-center gap-[9px]`. Context window, `$in / $out per Mtok`, thinking style —
each 11.5px `t-muted` tabular-nums, separated by `·` in `text-b-strong`. The right slot
holds either the capability hint ("Adaptive-thinking models do not accept temperature.")
or the dirty marker: 5px `bg-j-primary` dot plus "Changed — not saved" in 11.5px
`j-primary`.

### 9.6 Warning variant (§4.4)

The same card with three substitutions and no reflow:

- card border → `border-j-warning/35`
- meta row `border-t` → `border-j-warning/25`
- Model control border → `border-j-warning/45`, and the derived provider name renders
  `text-j-warning` instead of `t-muted`

The meta row is replaced by the warning row: a 14px warning glyph, the consequence at
12px `text-j-warning`, then a `md` ghost button (`border-j-warning/40 text-j-warning`)
labelled `Connect {provider}`, which switches to the Providers tab with that provider
pre-expanded.

### 9.7 Save bar

Pinned footer, outside the scroll region. `border-t border-b-secondary`,
`bg-surface-2/50`, padding `12px 24px`, `flex items-center gap-3`.

Left: 6px `bg-j-primary` dot plus "N unsaved change(s)" in 12.5px `j-primary`. When
clean, that is replaced by "No changes" in 12.5px `t-muted` and **both buttons are
disabled** at `opacity-45`. Right: `Discard` (ghost `md`) then `Save changes` (primary
`md`), `gap-2`. Exactly one per tab (**F2**, **F3**).

### 9.8 Providers tab

**Toolbar** — padding `14px 24px 12px`, `flex items-center gap-[10px]`. Search is a `ctl`
at `flex-1` with `justify-start gap-[9px]`, a 13px leading glyph, placeholder "Search N
providers". Segmented filter: `bg-surface-2`, 1px `border-b-secondary`, radius 8px,
padding 3px, `gap-[2px]`; each segment 28px tall, padding-x 12px, radius 6px, 12.5px;
selected `bg-surface-4 text-t-primary font-medium`, unselected `text-t-tertiary`.

**Group header** (`grp`) — `flex items-center gap-2`, padding `0 2px 8px`: a `sec-h`
label and a neutral count chip. Groups are `Connected` then `Available`; the connected
card carries `margin-bottom: 18px`.

**Provider row** (`prow`) — `flex items-center gap-[13px]`, padding `11px 20px`,
separated by a 1px `bg-b-secondary` rule rather than a border, so the card's radius stays
clean.

| Slot | Spec |
|---|---|
| status dot | 7px, per §9.3 |
| name | **fixed 150px**, 14px/500, ellipsis — fixed so chips align down the column |
| auth-kind chip | neutral, from `CatalogProvider.auth_kind` |
| status chip | success `valid` / warning `unreachable` / outline `Not connected` |
| detail | 11.5px `t-muted`; a base URL renders in `font-mono` |
| actions | right, `gap-[7px]`, `sm` ghost buttons |

Actions by state: connected → `Test`, `Edit`, plus `Remove` **only** when
`source === "workspace"` (preserving today's correct behaviour); env or
deployment-default → `Test`, `Override`; not connected → `Connect`.

**Expanded row:** `bg-j-primary/5` with a **2px left border** in `j-primary`. Its header
row is unchanged except the action becomes `Cancel`, plus a status chip stating why it
opened ("Needed by the Fast tier") when reached from a warning card. Form: padding
`0 20px 15px`, fields in `grid-cols-2 gap-[12px]` (`grid-cols-1` on mobile) generated
from `CatalogProvider.credential_fields`, `margin-bottom: 11px`. Footer row: a 12px lock
glyph and "Encrypted at rest. Never shown again after saving." (11.5px `t-muted`), with
`Save & test` (primary `md`) right. Expansion is exclusive.

A 56px `bg-gradient-to-b from-transparent to-surface-1` overlay sits at the bottom of the
scroll region so a long list reads as continuing.

### 9.9 Model picker

A centred command palette over the modal, **not** a dropdown anchored to the field.

```
width 560px, top 78px, horizontally centred
bg-surface-1, 1px border-b-strong, radius 14px
shadow 0 24px 60px rgba(0,0,0,.55)
```

The modal behind dims with `bg-surface-0/55`. On mobile the palette is a full sheet.

| Region | Spec |
|---|---|
| Search row | padding `14px 16px`, `gap-[11px]`, `border-b border-b-secondary`; 15px glyph; 15px `t-muted` placeholder "Search models by name, provider, context or price"; right: active tier name (11px `t-muted`) and an `esc` `kbd` |
| Results | `max-height: 474px`, scrolls |
| Group header | `grouphdr`: 10px uppercase `t-muted`, padding `11px 16px 6px`, `flex gap-2`, trailing 1px hairline, optional right-aligned "N models" (11px, sentence case) |
| Row (`mrow`) | `flex items-center gap-3`, padding `9px 16px` desktop / **`12px 16px` below `sm`**, 13.5px |
| Row — selected | `bg-j-primary-soft`, **2px left border** `j-primary`, `padding-left: 14px`, leading 13px check glyph in `j-primary`, name at weight 500 |
| Row — unselected | `padding-left: 16px` plus a 13px leading spacer, so names align with the selected row |

Columns after the name (`flex-1`, ellipsis) are all `flex-shrink-0`, right-aligned,
11.5px `t-muted` tabular-nums: thinking-style `tchip`, context **52px**, cost **96px**,
provider **66px** — the provider column renders only in the "Suggested" group, the one
group whose rows cross providers. A context window of 1M or more renders
`text-j-primary`: the single place colour marks a *value* rather than a state.

Sections in order: **Suggested for {tier}** (from `suggested_tier`), then one group per
connected provider in catalog order.

**Footer:** `border-t border-b-secondary`, `bg-surface-2/60`, padding `9px 16px`. Left:
`↑` `↓` kbd "navigate", `↵` kbd "select". Right: **"N providers not connected: X, Y"** —
naming up to two, then `+N more` (11.5px `t-secondary`) — and a `Browse all providers`
primary `sm` button switching to the Providers tab.

> **Amended during implementation.** This section originally read "N providers not
> connected" with no names, and gave the row a flat `9px 16px`. Both were corrected against
> other sections of this same spec rather than against taste:
> - **Naming** serves §4.7's own stated purpose — unconnected providers are "surfaced, not
>   filtered out of existence … the prerequisite is invisible until it fails". A bare count
>   still leaves the founder unable to learn *which* model they are missing.
> - **12px rows below `sm`** follow §9.10, whose mobile table is uniformly 44px targets.
>   Choosing a model is the single act this surface exists for; a 36px target for it on a
>   phone is not a case where "§9.10 lists controls, not rows" should win. Desktop keeps
>   §9.9's 9px to the pixel.

Keyboard: `↑`/`↓` move, `↵` selects, `Esc` closes without changing the binding, typing
filters. `role="listbox"` with `aria-activedescendant` on the input (**A3**).

### 9.10 Mobile (below `sm`, 640px)

| Element | Override |
|---|---|
| Shell | full sheet, `inset-0`, no radius |
| Rail | becomes the sheet's **root view** — a pushed list, not a scroller (**L4**) |
| Pushed header | padding `14px 12px 14px 8px`; back control `‹ Settings` in `j-primary`, 15px, 44px tall; centred title 16px/600; 44×44 close |
| Body | padding `18px 16px`, `gap-[18px]`, `bg-surface-0` |
| Tier card | padding `14px 16px 12px` |
| Field grid | `grid-cols-2 gap-[10px]`; Model and Temperature `col-span-2` |
| Controls | 44px tall, 15px, padding-x 12px |
| Buttons | 44px tall |
| Meta row | `line-height: 1.6` — it wraps to two lines |
| Save bar | `bg-surface-2`, padding `12px 16px 26px`; the extra bottom padding clears the home indicator |

No fake status bar and no fake keyboard — the real ones render over the layout.

### 9.11 Icons

Inline stroke SVG only, no icon library, matching the existing `TabIcon` in
`settings-modal.tsx`: `fill="none"`, `stroke="currentColor"`, round caps and joins.

| Icon | viewBox | Stroke | Path |
|---|---|---|---|
| Rail tabs (existing six) | `0 0 16 16` | 1.4 | unchanged from `settings-modal.tsx` |
| **Providers** (new) | `0 0 16 16` | 1.4 | `M5.5 2.5v3M10.5 2.5v3` · `M3.5 5.5h9v2a4.5 4.5 0 01-9 0v-2z` · `M8 12v1.5` |
| Search / picker trigger | `0 0 12 12` | 1.4 | `circle cx=5.2 cy=5.2 r=3.3` · `M7.7 7.7l2.1 2.1` |
| Chevron down (select) | `0 0 10 10` | 1.3 | `M2.5 4l2.5 2.5L7.5 4` |
| Chevron right (row, disclosure) | `0 0 14 14` | 1.5 | `M5.5 3L9.5 7l-4 4` |
| Chevron left (back) | `0 0 14 14` | 1.6–1.7 | `M8.5 3L4.5 7l4 4` |
| Check (picker selection) | `0 0 14 14` | 1.8 | `M2.8 7.4l2.7 2.7 5.7-6` |
| Warning | `0 0 14 14` | 1.5 | `circle cx=7 cy=7 r=5.6` · `M7 4.5v3.2M7 9.9v.2` |
| Lock (credential hint) | `0 0 14 14` | 1.4 | `rect x=2.5 y=6 w=9 h=6 rx=1.5` · `M4.75 6V4.25a2.25 2.25 0 014.5 0V6` |
| Close | `0 0 16 16` | 1.5 | `M4 4l8 8M12 4l-8 8` |

The `▾ ▸` text glyphs in today's Advanced disclosure are replaced by the chevron above.
No emoji or dingbats anywhere in this surface.
