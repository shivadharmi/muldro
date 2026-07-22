# Step 10D · A-5 — Deep-Chat Single-Lead Restructure (design + decomposition)

> **Status:** DESIGN LOCKED (2026-07-11), NOT yet implemented. Supersedes the original 10D plan's A-5/B5 ("chat_processor deep branch — drop presenter step"), which was found UNSAFE at execution. Implement via superpowers:subagent-driven-development, decomposed 5a → 5b → 5c → (5d), each dormant + byte-neutral on legacy.

## Why this doc exists (the finding that reshaped A-5)

The original 10D A-5 (B5) premise — "on `deep` the deep lead already formatted the reply inline, so DROP the explicit presenter step" — is **false for the current architecture**. `chat_processor.process_message_stream` runs a **per-step loop even on `deep`** (one `call_agent_stream` per plan step + a separate planner call + an explicit presenter call). The user-facing reply (`Presentation`) is yielded at only TWO sites: `chat_processor.py:~592` (`direct_answer`, single-read Perceiver plans only) and `:~619` (the explicit presenter step). The per-step loop never yields a reply.

⟹ Naively dropping the presenter step on `deep` would blank the reply on ~every non-single-read plan shape (knowledge-only, `system.respond`-terminal [no-op], read+write, pure-write, even fast reason/respond). The single-deep-lead model that B2/B3/B4/librarian-middleware were built for **was never wired into the chat path.**

**USER DECISION (2026-07-11): FULL RESTRUCTURE** — on `deep`, replace the per-step loop + explicit presenter step with a SINGLE deep lead over the whole goal, reply inline. This finishes the deep-chat migration and enables B7's true 6→4 (retires the presenter agent as a chat route).

## Locked decisions

| # | Decision | Choice |
|---|---|---|
| Restructure | per-step loop → single deep lead on `deep` | **YES (full restructure)** |
| Planner | keep or drop on `deep` | **KEEP** — produces the plan surface (SSE `plan` event, persistence) AND bounds the lead's capability_scope |
| Lead identity | which agent is the lead | **synthetic per-turn `lead` SubAgent** (new `LEAD_PROMPT`, `name="lead"`), not an existing agent |
| Model tier | lead tier | **sonnet** always (revisit opus-for-critical later) |
| Capability scope | how the lead is bounded | **UNION of the plan's step capabilities** (plan-bounded), fail-closed via `capability_scope_guard`. Accepted (tighter on writes than executor's full-union; read-only plans stay hard read-only) |
| Exactly-once on writes | dedup chat writes | **write_lock + LEAD_PROMPT rule ONLY** (no ledger). ⚠️ ACCEPTED RESIDUAL RISK: a misbehaving react-loop could double-write (e.g. send email twice); no durable dedup backstop. **REVISIT at R2 chat-flip gate before real traffic.** |
| Auth | trust gate on chat lead | **UNGATED** (`DIRECT_USER_REQUEST`, trust_gate dormant) — user's message = authorization (CLAUDE.md invariant). capability_scope is the compensating control. |
| Mode | which modes use single-lead | **`mode=="ask"` (interactive stream) ONLY.** `plan`/`execute` keep the per-step loop (so risky-step approval isn't bypassed). |
| Gating | dormant flag | new **`deep_single_lead: bool = False`** (`JARVIS_DEEP_SINGLE_LEAD`), + resolved `runtime=="deep"` via a new `AgentInvoker.effective_chat_runtime()` accessor. Legacy per-step loop UNCHANGED. |
| Flag subsumption | | single-lead path SUBSUMES `deep_inline_format` (B2 always-on for the lead). `deep_delegates_enabled` (B3) + `deep_readback_enabled` (B4) stay independent, compose on the lead build. |

## Design (the target shape)

### Reuse
No chat single-lead exists today. The reusable primitive is the `runtime=="deep"` branch body of `call_agent_stream` (`agent_invoker.py:~678-764`): it already builds ONE deep agent (`_build_deep_agent_for(authorization_source=DIRECT_USER_REQUEST)`) and streams frames. Generalize it into a new thin method **`AgentInvoker.stream_deep_lead(...)`** that runs once over the whole goal with the synthetic lead. Do NOT reuse `run_autonomous_deep_step` (autonomous differs on every axis: AUTONOMOUS auth + trust_gate active, ledger-wrapped, dict output, pre-approved-cap bound, per-step grain).

### Flow (`_process_core` on the interactive stream path)
```
intent classify → (fast plan | Planner) → PlanReady/persist/log   [UNCHANGED]
  ├─ legacy:  per-step loop → direct_answer|presenter → learner    [UNCHANGED, byte-neutral]
  └─ deep AND deep_single_lead AND mode=="ask":
        lead, tools = build_chat_lead(plan, agents, cheap_mode) + resolve tools
        async for frame in invoker.stream_deep_lead(
              lead, tools, message=<RAW user message>,
              context_block=<assembled ctx + history + plan summary>,  # NOT in the human message (keeps middleware source clean)
              user_id, workspace_id, intent=intent, trace=trace):
            yield agent_event_from_sse(frame)
            if frame["event"]=="agent_done":
                yield Presentation(text=strip_surface_blocks(frame["text"]))
        # surface push from reply (extract_surface_spec) — same as today
        # NO explicit presenter step; NO InteractionLearner spawn (middleware does it faithfully)
```

### The synthetic lead
- `SubAgent` (dataclass, `agents.py:~192`) built per turn — `_build_deep_agent_for` takes an agent OBJECT, so no registry entry needed. `get_tools_for_agent` + capability_scope guard both read `agent.capability_scope`.
- `prompt` = `JARVIS_SOUL_CORE` + new `LEAD_PROMPT` ("handle the whole turn: gather via tools, act, and ALWAYS end with a user-facing reply" — the terminal-message rule is load-bearing, see spike).
- `capability_scope` = `derive_lead_scope(plan.steps)` (pure fn): per jarvis step — real capability → add it + `resolver.resolve_for_step`; `perceive` → perceiver read scope; `knowledge.search` → `internal.search` family; `reason`/`respond`/`system.*`/`none` → ∅.
- PRESENTER_VOICE: `stream_deep_lead` ALWAYS applies `_augment_system_blocks_for_inline(..., is_reply_lead=True)` → decouples reply-lead from `=="presenter"` (resolves the A-3 note at `agent_invoker.py:88-91`).

### Composition (all on the ONE lead build, already inside `_build_deep_agent_for`)
- Delegates (B3): lead is the `task`-tool host; A-4's delegation instruction now correctly targets THIS lead.
- Readback (B4): `deep_readback_enabled` inserts `read_back` into `gated_chain` on the lead.
- Librarian middleware: built once → fires once (terminal-round guard). FIDELITY FULLY CLOSED: raw message as human input + real `intent`/`trace_id` threaded via new `_build_deep_agent_for(librarian_active, learn_intent)` params → restores `SKIP_LEARNING_INTENTS` gate + correct provenance. Drop the `InteractionLearner` spawn on this branch.

### Reply coverage (no shape reply-less)
single-read → lead reads+synthesizes; knowledge-only → `internal.search`+answer; system.respond-terminal → reads+narrates (no-op step gone); read+write → reads+writes+narrates; **pure-write → writes then narrates the confirmation** (⚠️ the spike-risk case); fast reason/respond → replies tool-free.

## ⚠️ SPIKE REQUIRED before 5b lands
**Highest-risk unknown:** does a deepagents react-loop lead reliably emit a terminal user-facing message AFTER a pure write (vs. ending on the tool result)? `LEAD_PROMPT` rule + PRESENTER_VOICE should force it, but PROVE it with a scripted-plus-real-model spike, do not assume. If it doesn't hold, need a fallback (e.g. a forced synthesis turn after the last tool call).

## Decomposition (implement in order, each dormant + byte-neutral on legacy)

- **5a — Lead builder + streaming primitive (foundation, NO chat wiring → fully dormant).**
  - `derive_lead_scope(plan.steps) -> set[str]` (pure) + `build_chat_lead(plan, agents, cheap_mode)` synthetic-SubAgent factory + `LEAD_PROMPT` (with the terminal-message rule).
  - `AgentInvoker.stream_deep_lead(...)` reusing `_build_deep_agent_for` + `stream_deep_agent_events` (always PRESENTER_VOICE, DIRECT_USER_REQUEST).
  - `AgentInvoker.effective_chat_runtime()` accessor (`effective_runtime("chat", redis=self._services.extras.get("redis"), settings)`).
  - **SPIKE** the pure-write terminal-message risk here.
  - Tests: scope derivation, lead streams a reply, PRESENTER_VOICE applied.
- **5b — chat_processor branch + capability-scope wiring (SECURITY-CRITICAL review checkpoint).**
  - Add the `deep AND deep_single_lead AND mode=="ask"` branch calling the lead path instead of the per-step loop + presenter. Keep the `InteractionLearner` spawn for now (safe overlap — middleware still OFF here).
  - Tests: reply coverage per shape, capability-scope enforcement (read-only plan → write denied, fail-closed), legacy byte-neutral, mode guard (`plan`/`execute` keep per-step loop).
  - 2-stage PARALLEL review + security-reviewer (capability-scope + ungated write path).
- **5c — Librarian fidelity + drop learner.**
  - Add `librarian_active` + `learn_intent` params to `_build_deep_agent_for`; `stream_deep_lead` activates the middleware with real intent/trace; drop the `InteractionLearner` spawn on the single-lead branch. Resume/autonomous/shadow keep `librarian_active=False` default (byte-neutral).
  - Tests: single-fire, faithful extraction (raw message + reply + real intent), trivial-intent skip.
- **5d — (optional/last) compose delegates/readback + retire per-step deep inline-format redundancy.** Defer to when B3/B4 activate.

## Cascade to B7 (R5)
With the presenter step retired on `deep` (single lead IS the reply-lead), the presenter AGENT is no longer a chat route once chat flips to deep. Combined with A-8 (generate_briefing off the presenter agent), B7's **6→4** (drop presenter + librarian rows) becomes sound — presenter droppable after chat clears (R2) + A-8; librarian after perception clears (R3). This is what the full restructure buys vs. the librarian-only fallback (which would have forced 6→5).

## Accepted risks (revisit at R2 chat-flip gate)
1. **Exactly-once:** write_lock + prompt only, NO ledger. A react-loop could double-write. ACCEPTED; revisit before real traffic at R2.
2. **Per-turn blast radius:** the lead holds the plan-union capability_scope in one ungated turn (vs per-step). Plan-bounded + fail-closed. ACCEPTED.
3. **Pure-write terminal message:** must be spike-proven in 5a.
