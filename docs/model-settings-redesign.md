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

Four forks were settled during design. They are not re-opened by implementation.

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

### 2.4 A tier bound to an unconfigured provider warns; it does not block

The save proceeds — the founder may be about to connect the provider — but the warning
is persistent, server-computed, and states the real consequence (§4.4). It is not
advisory text: per **B3**, the run-time failure is total.

---

## 3. Defects to fix

All twenty are verified against source at the cited locations. Severity: **S1** = data
loss or run-time failure, **S2** = user-visible incorrectness, **S3** = quality.

### 3.1 Backend / data integrity

| ID | Sev | Defect |
|----|-----|--------|
| **B1** | S1 | **Saving a key wipes `base_url` and `extra_config`.** `put_provider_credential` assigns `existing.base_url = body.base_url` and `existing.extra_config = body.extra_config` unconditionally. `api.ts:1097` sends `base_url: undefined` (dropped by `JSON.stringify`) and never sends `extra_config`. Rotating an Anthropic key clears its custom base URL; saving Ollama without retyping its URL unconfigures it, because `base_url` is its only credential. |
| **B2** | S1 | **`ProviderStatus` never exposes `base_url`.** The GET returns `provider/configured/status/source` only, so the UI cannot display the URL in effect nor round-trip it. With B1 this makes editing any configured provider destructive-by-default. |
| **B3** | S1 | **A tier may be bound to an unconfigured provider, and it hard-fails at run time.** `put_config` validates only that `get_model_spec(provider, model_id)` resolves. `ModelResolver.resolve` then raises `ModelConfigError("provider X is not configured")` (`model_resolver.py:95`). The degradation path at `model_resolver.py:178` covers **agent overrides only** — it falls back to the agent's tier row. A *tier* row has nothing to fall back to. |
| **B4** | S2 | **`effort: "none"` is a legal contract value the UI cannot render.** `TierBinding.effort: str = "none"`; `EFFORT_OPTIONS = ["low","medium","high"]`. `addOverride` seeds `"none"` when no tier binding is found, so the `<select>` shows `low` while state holds `none`, and Save persists `none`. `effort` is an unvalidated `str`. |
| **B5** | S2 | **Catalog metadata is dropped at the API boundary.** `ModelSpec` carries `context_window`, `input_cost_per_1k`, `output_cost_per_1k`, `supports_prompt_cache`; `CatalogModel` exposes none. There is no provider-level object at all (`providers: dict[str, list[CatalogModel]]`), so the UI has no display name, auth shape, or model count — it renders raw slugs like `google_genai`. |
| **B6** | S3 | `_provider_statuses` iterates `for provider in MODEL_CATALOG`, so a credential row for a provider absent from the catalog is invisible and unmanageable. |

### 3.2 Frontend correctness

| ID | Sev | Defect |
|----|-----|--------|
| **F1** | S2 | **Changing provider blanks the model, and Save then 400s.** `model-tab.tsx:96` sets `model_id: ""`; nothing gates Save; `put_config` rejects with `unknown model <provider>/`. |
| **F2** | S2 | **Two Save buttons, identical behaviour.** "Save" and "Save overrides" both call `handleSave()`, which posts tiers *and* overrides. Either button saves both sections. |
| **F3** | S2 | **No dirty tracking.** Save is always enabled, nothing indicates a pending edit, and closing the modal discards silently. |
| **F4** | S2 | **Conditional controls reflow the row.** `showEffort` / `showTemperature` unmount their controls when the model doesn't support them, so switching model changes the control count and shifts everything after it. |
| **F5** | S3 | **The API key stays in component state after saving.** `ProviderRow` never clears `apiKey`, so the secret lives in React state and in the DOM input for the modal's lifetime. |
| **F6** | S3 | **`keyOf()` is `binding.tier` for tiers *and* agent overrides.** An agent named `reasoning`/`balanced`/`fast` collides in `updateTier`/`updateOverride`. |

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

Per §2.4 and **B3**. Computed **server-side** so it survives a reload and cannot drift
from the resolver's own rule:

```python
class ConfigWarning(BaseModel):
    scope_type: Literal["tier", "agent"]
    scope_key: str
    code: Literal["provider_not_configured"]
    message: str
```

`ModelConfigResponse` gains `warnings: list[ConfigWarning]`, populated on **both** GET
and PUT by re-using `ModelResolver.resolve_credential` — the same call the run-time path
makes, so the two cannot disagree.

A warned tier card renders with an amber border and a footer row stating the real
consequence — *"Groq is not connected. There is no tier fallback — every agent on Fast
will fail until you connect it."* — plus a **Connect Groq** action that switches to the
Providers tab with that provider pre-expanded.

The copy must not promise a fallback. There is none for tier bindings.

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

class TierBinding(BaseModel):
    ...
    effort: Literal["none", "low", "medium", "high"] = "none"   # was bare str — B4
```

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
- `warnings` is populated for a tier whose provider has no resolvable credential, on
  both GET and PUT, and agrees with `ModelResolver.resolve_credential`.
- `effort` outside the Literal is rejected with 422.
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
   catalog fields, `base_url`/`extra_config_keys` on `ProviderStatus`, the `effort`
   Literal, `warnings`, the partial-update fix, B6. Tests first.
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
- Any change to `ModelResolver` precedence or the agent-override degradation path. B3 is
  fixed by warning at the UI/API boundary, not by inventing a tier-level fallback —
  a silent tier fallback would hide a misconfiguration behind a model the founder did not
  choose and is not paying attention to.

---

## 8. Judgement calls flagged for the founder

- **F6 (`keyOf` collision).** The honest fix renames `TierBinding` to a discriminated
  `scope_type` + `scope_key` shape, matching the DB, and drops the comment in
  `contracts/model_config.py` apologising for reusing `tier` to carry an agent name. That
  touches contracts, the service, the routes and the frontend types. Contained, and
  pre-launch is the cheapest it will ever be — but it is scope beyond the layout brief,
  so it is called out rather than assumed. The fallback is an in-memory
  `` `${scope}:${name}` `` key, which fixes the collision without touching the contract.
- **B3 policy.** Warn-and-allow is specified. Reject-on-save is the stricter alternative
  and would make the failure impossible rather than merely visible, at the cost of
  blocking a legitimate connect-next-then-bind order.
