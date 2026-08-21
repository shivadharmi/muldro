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
