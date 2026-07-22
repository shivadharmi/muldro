# Spike: prompt caching survives the explicit `middleware=` shape + cache observability

**Date:** 2026-06-28 · **Task:** Step-0 rebuild, Task 8
**Status:** ⛔ **BLOCKED — pending infra (no Anthropic API key in this environment).** Not run; NOT faked.

## Why blocked
Verifying that prompt caching works requires a **real Anthropic API call** to observe
`cache_read_input_tokens` on a second turn — there is no `JARVIS_ANTHROPIC_API_KEY` /
`ANTHROPIC_API_KEY` in this environment. The number must come from an actual two-turn run, so it is
deferred — not estimated.

## The spike question (to answer when a key is available)
The spec assumes prompt caching keeps working on the `deep_runtime` path even though
`build_deep_agent` passes an **explicit `middleware=` list** to `create_deep_agent` (which could
displace the auto-injected `AnthropicPromptCachingMiddleware`). Verify it survives.

## Probe to run (when an API key is available)
Create `backend/spikes/caching/probe.py`:
- Build a `deep_runtime` agent via `build_deep_agent` with a **large, stable system prompt**.
- Run two turns on the same thread; capture usage from each.
- **Acceptance:** 1st turn shows `cache_creation_input_tokens > 0`; **2nd turn shows
  `cache_read_input_tokens > 0`.**

Run:
```bash
cd backend && source .venv/bin/activate
JARVIS_ANTHROPIC_API_KEY=... python -m spikes.caching.probe
```

## If caching does NOT survive
Explicitly add `AnthropicPromptCachingMiddleware` to the `build_deep_agent` middleware list (ahead of
the policy middlewares), and re-verify `cache_read_input_tokens > 0` on turn 2.

## Cache observability (offline-doable; pair with this spike)
Independently of the API check, add cache observability so this stays visible in production:
- In the deep_runtime usage/budget middleware (`after_model` / `wrap_model_call`), record/log
  `cache_read_input_tokens` and `cache_creation_input_tokens` per model call.
- TDD: a test asserting the usage span/log carries those two fields.
This part needs no API key and can land in a later Step-0/Step-8 pass; the **end-to-end caching
proof** above remains gated on credentials.

## Downstream
Feeds **Step 8** (context JIT-hybrid relies on a stable cached prefix). Until verified, treat
"caching works on the deep_runtime path" as **assumed, not confirmed**.
