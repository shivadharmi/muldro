# Cost & Model Tiers

Jarvis routes each of its 7 agents to a model *tier* (`opus` / `sonnet` / `haiku`)
chosen for that agent's job. Tier → concrete model is resolved in
`orchestrator/jarvis.py` (`MODEL_TIERS` for the direct API, `BEDROCK_MODEL_TIERS`
for Bedrock). This page documents what that costs and how to lower it.

## Per-tier list prices

List prices for the Claude 4.x family (USD per million tokens). Tiers map to the
current default models (`claude-opus-4-8`, `claude-sonnet-4-6`,
`claude-haiku-4-5`):

| Tier   | Default model        | Input $/M | Output $/M | Used by (default) |
|--------|----------------------|-----------|------------|-------------------|
| opus   | claude-opus-4-8      | 15.00     | 75.00      | Planner |
| sonnet | claude-sonnet-4-6    | 3.00      | 15.00      | Perceiver, Librarian, Governor, Operator, Presenter |
| haiku  | claude-haiku-4-5     | 1.00      | 5.00       | Persona |

Thinking tokens are billed as **output**. With per-agent thinking budgets of
2K–8K tokens, the Opus Planner is by far the most expensive single call —
Opus output is 5× Sonnet, and the Planner has the largest thinking budget (8192).

## Per-message cost (estimates)

From the OSS-release audit, a typical multi-step message that wakes the Planner:

| Mode             | ~Cost / message | Notes |
|------------------|-----------------|-------|
| Default          | ~$0.50          | Opus Planner (8K thinking) + Sonnet execution + Presenter |
| Cheap mode       | ~$0.17 (−65%)   | No Opus (opus→sonnet) + halved thinking budgets |

Fast-intent messages (greeting, chitchat, single read, …) skip the Planner
entirely and cost a fraction of the above.

## Daily budget

`JARVIS_DAILY_TOKEN_BUDGET_USD` (default **$25**) is the daily spend ceiling
before the system degrades. The previous $5 default silently degraded after only
2–3 Planner-backed messages, so it was raised to $25 — roughly a day of active
dogfooding at default-mode prices, or ~3× that in cheap mode.

`BudgetTracker` hydrates its counter from the DB on day change, so the ceiling
survives restarts.

## Cheap mode

Set `JARVIS_CHEAP_MODE=true` to apply a cost-reduced preset to every agent:

- **No Opus** — the `opus` tier is downgraded to `sonnet` (the ~65% lever).
  Haiku is left as Haiku (it is already cheaper than Sonnet).
- **Halved thinking budgets** — each agent's thinking budget is cut in half,
  floored at 1024 tokens so reasoning-heavy agents keep a usable scratchpad.

Implementation: `apply_cheap_mode()` / `build_agent_set()` in
`orchestrator/agents.py`, applied where the orchestrator builds its agent set
(both the hardcoded defaults and the DB-loaded set). The transform is pure and
returns new `SubAgent` objects — it never mutates the shared `AGENTS` singleton.

Cheap mode trades some planning depth and answer polish for cost. Use it for
heavy dogfooding days or budget-constrained deployments; turn it off when you
want the Opus Planner's full reasoning.
