# Model-selection benchmark

Scores a candidate model on what Muldro's code actually demands of one, so a model
decision rests on measurement rather than reputation.

```bash
docker compose up -d                                        # tool schemas come from the live registry
cd backend
uv run python -m scripts.model_bench --list
uv run python -m scripts.model_bench.selftest               # prove the scorers first
uv run python -m scripts.model_bench --candidate openai/gpt-5-mini --trials 3
```

Keys are read from `backend/.env`. Each provider is tried under the `MULDRO_` prefix
first and then bare — `MULDRO_ANTHROPIC_API_KEY`, `MULDRO_OPENAI_API_KEY` /
`OPENAI_API_KEY`, `MULDRO_OLLAMA_API_KEY` / `OLLAMA_API_KEY` — so the bench keeps
working whichever naming the app settings use. A candidate whose key is missing under
both names fails loudly and is skipped.

## The tasks

| key | tier | asks |
|---|---|---|
| `A_wide_read` | balanced | with **33 tools bound**, does it call a mail tool at all? |
| `B_narrow_write` | balanced | with 7 tools bound, does it call `store_memory` carrying the fact? |
| `C_plan_json` | reasoning | does the reply validate as `PlanOutput` without `extract_plan`'s text fallback? |
| `D_terminal_reply` | balanced | after acting, does it end the turn speaking to the user? |
| `E_no_fabrication` | balanced | given an **empty** inbox, does it invent one? |

A and B are a matched pair: a model that passes B and fails A has a context-width problem,
not a tool-use problem. That is the shape llama3.1:8b failed in.

`E` is an automatic fail and is never traded against anything — `soul.md` law 1.

## What is real, and what is not

Real: the tool schemas (resolved from the live registry for the task's capability scope),
the composed system prompt, the deep-agent graph, the `capability_scope` guard, the
virtual-filesystem suppression, and the tool dispatcher.

Stubbed: `execute_tool`. Every tool call returns a fixed result, so runs are deterministic,
nothing external is touched, and an empty inbox makes fabrication detectable.

Not exercised: the write gates (`trust_gate` / `permission_gate`), which decide whether a
call EXECUTES rather than whether a model CHOOSES to call; and `ModelResolver` /
`build_model_kwargs` / the capability map, because candidates are constructed directly.
**A winning candidate still needs a `ModelSpec` in `src/config/model_catalog.py`** before
`PUT /v1/model-config` will accept it — that endpoint 400s on anything not in the catalog.

## Read the results per tier

The three tiers ask different things and need not share a model. `reasoning` (Planner) is
the hardest; `fast` (intent classification) is the easiest and highest-volume. Report a
per-tier binding, not an overall winner.

## Verify the checker

`selftest.py` feeds each scorer synthetic records it must accept and reject. Run it before
believing any score. It exists because the first run of this harness reported
`** FABRICATED **` for an account that was merely out of credits — the reporter had
inferred fabrication from "the fabrication task failed".
