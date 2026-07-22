# Phase 0 Spike — Deep Agents migration de-risk

Gate for the full migration described in
[`docs/deep-agents-migration-assessment.md`](../../../docs/deep-agents-migration-assessment.md).
Nothing in Phases 1–5 should start until both goals below pass (or the model-layer
fallback is scoped).

## What it proves
- **G1 — model layer:** `ChatAnthropic` can drive **Opus 4.8** with adaptive thinking +
  effort, replacing the hand-rolled `orchestrator/agent_loop.build_thinking_params`.
  *(Decision: direct Anthropic API, not Bedrock — so this is a confirmation, not a gamble.)*
- **G2 — middleware surface:** LangChain middleware (`@wrap_tool_call`, `@after_model`)
  can host Jarvis's two load-bearing per-call policies — **fail-closed capability-scope
  enforcement** and **token/cost capture**. If a deep agent can block an out-of-scope
  tool and observe per-call tokens, every other Jarvis policy (TrustEngine gate, budget,
  ContextPack, turn-scope) has a confirmed home.

## Run
```bash
cd backend
source .venv/bin/activate
pip install -r spikes/deepagents_phase0/requirements.txt
JARVIS_ANTHROPIC_API_KEY=<your-anthropic-key> python spikes/deepagents_phase0/spike.py
```

## Reading the result
- `G1 PASS` → delete `build_thinking_params`; pass `model="anthropic:claude-opus-4-8"`.
- `G1 FAIL` → small isolated fallback: a custom `BaseChatModel` wrapping the raw Anthropic
  client, keeping `build_thinking_params` verbatim. Migration still proceeds.
- `G2 PASS` → proceed to Phase 1 (build the middleware library).
- `G2 FAIL` → the failure is almost always a decorator **signature** mismatch (the spike
  marks each uncertain hook with `CONFIRM`); fix against the installed `langchain`
  version and re-run. This is the expected spike loop, not a stop signal.

## Notes
- These deps are **not** added to `backend/pyproject.toml` until the spike passes; pin
  exact versions then (see the assessment appendix).
- Live API calls may be blocked by the account session limit until it resets
  (was 7:20pm IST on 2026-06-22). Run after reset if you hit a session-limit error.
- This directory is a throwaway spike — it will be deleted in Phase 5 cleanup.
