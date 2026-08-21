# Cost & Model Tiers

Muldro routes each of its 6 agents to a provider-neutral model *tier* — `reasoning`,
`balanced`, or `fast` — chosen for that agent's job. The tier an agent runs at is code
(`AGENT_MODEL_TIERS` in `orchestrator/agents.py`). **Which model backs a tier is data**:
`ModelBinding` rows, seeded from `_DEFAULT_TIER_BINDINGS` (`services/model_config_registry.py`)
and overridable per workspace via `PUT /v1/model-config`. Provider capability facts live in
`config/model_catalog.py`; `config/capability_map.py` translates neutral inputs into
provider-specific kwargs, dropping any a given model would reject.

Anthropic backs every tier by default, but OpenAI, Gemini and local Ollama models are in the
catalog. This page documents what the **default** bindings cost and how to lower it.

## Tiers and default bindings

| Tier | Default binding | Effort | Used by (default) |
|------|-----------------|--------|-------------------|
| `reasoning` | anthropic / claude-opus-4-8 | high | Planner |
| `balanced` | anthropic / claude-sonnet-4-6 | medium | Perceiver, Librarian, Executor, Presenter |
| `fast` | anthropic / claude-haiku-4-5-20251001 | low | Persona |

Reasoning depth comes from the binding's `effort`, not from a per-agent token budget.
`AGENT_THINKING` still carries `budget_tokens` values, but only `thinking.enabled` reaches
model construction (`deep_runtime/model_factory.py`) — the numbers no longer size anything.

## List prices

Prices are USD per million tokens, from `MODEL_PRICING` in `orchestrator/budget.py` — the
single source of truth for cost attribution. Keep this table in sync with that dict.

| Default model | Input $/M | Output $/M |
|---------------|-----------|------------|
| claude-opus-4-8 | 5.00 | 25.00 |
| claude-sonnet-4-6 | 3.00 | 15.00 |
| claude-haiku-4-5-20251001 | 1.00 | 5.00 |

Thinking tokens are billed as **output**. Cached input is multiplied by `1.25` on write and
`0.10` on read (`CACHE_WRITE_MULTIPLIER` / `CACHE_READ_MULTIPLIER`).

The Planner is still the most expensive single call — it is the only agent on `reasoning`, and
it runs at `high` effort. But the gap is narrower than it used to be: Opus output is **~1.7×**
Sonnet, not 5×. Earlier revisions of this page quoted Opus at 15/75, which over-attributed its
cost by roughly 3× and made every derived estimate wrong; those per-message estimates have been
removed rather than re-guessed. Measure with the per-call `TokenUsage` spans the `budget`
middleware records instead.

## Daily budget

`MULDRO_DAILY_TOKEN_BUDGET_USD` (default **$25**) is the daily spend ceiling before the system
degrades. `BudgetTracker` hydrates its counter from the DB on day change, so the ceiling
survives restarts, and `record_from_span()` is the single write path.

## Cheap mode

Set `MULDRO_CHEAP_MODE=true` to downgrade the `reasoning` tier to `balanced`. Only the Planner
runs on `reasoning`, so this is the one lever that reaches the model; `balanced` and `fast`
agents are untouched. Because the downgraded tier also carries a lower `effort`, reasoning depth
drops with it — that follows from the tier change rather than from a separate control.

> Cheap mode once also halved each agent's `thinking.budget_tokens`. That lever went inert when
> the resolver started deriving depth from tier effort, and has been removed. Do not re-add a
> budget-halving step expecting it to reach the model.

Implementation: `apply_cheap_mode()` / `build_agent_set()` in `orchestrator/agents.py`. The
transform is pure and returns new `SubAgent` objects — it never mutates the shared `AGENTS`
singleton.

Cheap mode trades planning depth for cost. Use it for heavy dogfooding days or
budget-constrained deployments; turn it off when you want the Planner's full reasoning. A
cheaper option that does not touch quality is rebinding a tier to a different provider's model
in the model-config UI.
