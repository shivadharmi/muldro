# Step 7B2 — Deep-runtime DELEGATE layer (read-only subagents + per-child model + Governor delegate-critique)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. **Single-owner-per-file + SYNCHRONOUS implementer dispatch** (`run_in_background: false`) — a background SendMessage-resumed subagent once produced F811 duplicate defs (6B lesson); `agent_invoker.py` is the hot shared file, sequence its touches. **VERIFY-DON'T-TRUST every current-state claim against code before building on it** — this plan's anchors are `file:line` from `rebuild/first-principles` @ `80825fb`; re-confirm before editing. **Run the FULL non-e2e gate at EVERY checkpoint** (`uv run pytest tests/ --ignore=tests/e2e`), NOT the `tests/deep_runtime/` subset — 7B1 Phase-1's subset-green hid a break in a `tests/` root file. A "0 failed" gate with ~108 skipped means Postgres/Redis were DOWN (real-infra tests self-skipped) — restore infra, it is NOT a green gate.

**Goal:** Build the DEEP-RUNTIME machinery for the Step-7 DELEGATE collapse — read-only research delegates (Perceiver as the concrete instance) as depth-1 subagents on the deep chat lead via `create_deep_agent(subagents=…)`, each with its OWN capability-scope + dispatcher middleware and its OWN model tier; the ambient general-purpose `task`/GP subagent disabled; plus the NET-NEW Governor LLM delegate-summary critique (cheap Haiku pass, fail-open-annotated reads / fail-closed writes). All **DORMANT/proven** behind `JARVIS_RUNTIME=deep` (default `legacy`) AND a `deep_delegates_enabled=False` flag, NO runtime flip, chat path **byte-neutral on `legacy` and flag-off `deep`**. NO live lead→delegate routing decision (the single-lead concept = Step 8/10).

**Architecture:** The deep/legacy seam is `agent_invoker.call_agent_stream:396` (`if self._settings.runtime == "deep":`). Delegates are wired INSIDE the deep branch's build helper `_build_deep_agent_for:194-363` and the `build_deep_agent` factory, exercised only by forced/offline tests — the 6B/7B1 dormant-but-PROVEN pattern, not dead-wiring. The load-bearing unknown is NOT gating (a child's gate is baked into its own compiled middleware chain) but STREAMING: `deepagents` runs a subagent via `.ainvoke` inside its built-in `task` tool with `subgraphs=False` on the parent `astream`, so a child's *final* result deterministically returns as the `task` tool_result but its *token deltas* likely interleave into the parent stream mis-attributed to the lead. Phase 0 spikes this before anything is built. The Governor critique is a new lead-side `@wrap_tool_call` middleware — the ONE middleware that does NOT skip `task` — that reads the delegate summary out of the returned `Command` and annotates it (cloning the RiskAssessor Haiku-side-call pattern, NOT the plan-shaped Governor service).

**Tech Stack:** Python 3.13 (venv is 3.13), async SQLAlchemy (asyncpg), LangGraph 1.2.6 / `deepagents` 0.6.11 (langchain 1.3.10, langchain-core 1.4.8), langchain middleware (`@wrap_tool_call`), `ChatAnthropic` (a `BaseChatModel`), pytest (custom `pytest_pyfunc_call` asyncio hook — NO pytest-asyncio), `uv` (NO pip; `uv sync --all-extras`). Full gate: `uv run pytest tests/ --ignore=tests/e2e` from `backend/`.

**Baseline at plan time:** `rebuild/first-principles` @ `80825fb`; **3260 passed / 18 skipped** (full-infra green; 18 — NOT ~108 — confirms Redis/Postgres/Qdrant up); single alembic head `1a2770a28c39`; `alembic check` drift-free; ruff clean. **Expect NO migration in 7B2** (no agent removed; Perceiver is REUSED as a delegate, not a new DB row; head stays `1a2770a28c39` — VERIFY at the end).

**INFRA GOTCHA (this env):** Jarvis `redis_url`→`localhost:6379` is served by a DIFFERENT project's `hyperlocal-redis` container, NOT Jarvis's own `jarvis-redis-1` (which exposes `6379/tcp` internally but does NOT publish the host port). `docker compose up -d postgres redis qdrant`; if `:6379` is refused, `docker start hyperlocal-redis`. Use UUID-suffixed keys in Redis tests.

---

## 0. How this fits the rebuild (context — READ FIRST)

Step 7 (spec T1) collapses the 6 cognitive agents into one lead + read-only workers, cognition moving to middleware/tools/jobs, PRESERVING model/budget specialization. 7A shipped (Persona full-trace + dead-Governor-agent kill 7→6). 7B1 shipped (Presenter inline / Librarian extraction middleware / Governor deep-audit middleware / fold 6C #1) — all dormant/proven. 7B2 is the DELEGATE layer. 7C is inline read-back + wiring `budget`/`unavailable_server`.

**Forks resolved this session (via AskUserQuestion):**
- **Dormancy = SCAFFOLD-ONLY, PROVEN OFFLINE.** Build `subagents=` support + per-child gated middleware + per-child model + disable `task`/GP; prove via a forced OFFLINE test that a subagent runs GATED (its read tool scoped + centrally dispatched). Do **NOT** wire a live lead→delegate routing decision (which agent delegates, when = the single-lead concept = Step 8/10). Matches the 6A/6B/6C/7B1 rhythm.
- **Packaging = ONE COMBINED 7B2 plan** (scaffolding + Governor critique in a single plan; phase ordering respects that the critique depends on delegates existing).
- **Critique = LLM, CHEAP MODEL (Haiku).** A Haiku review of the delegate's returned summary, lead-side `@wrap_tool_call` on `task`; fail-open-ANNOTATED ("unreviewed") for reads, fail-closed for writes (defensive — 7B2 delegates are read-only). Decoupled from 7C's `ReadBackVerifier` (Fork-4 locked).
- **Delegate instance = REUSE PERCEIVER config.** Register the existing read-only Perceiver (sonnet/6144, zero external-write caps) as the concrete delegate on the deep CHAT lead; the perception-path Perceiver (`call_agent`, always legacy) is untouched. No new agent row.

**Why this is dormant even when `deep`:** delegates are registered only when `deep_delegates_enabled=True` (default `False`). Flag-off `deep` = byte-identical to 7B1 (no `subagents=`, ambient GP `task` unchanged). Flag-on is exercised ONLY by forced/offline tests. Live activation (flip the flag + build the single-lead routing) is Step 8/10.

---

## 1. Ground-truth current state (verify-don't-trust anchors)

All `file:line` from `backend/` @ `80825fb`, cross-verified by 4 parallel extraction passes this session. Re-confirm before editing. **Note:** `agent_invoker.py` lives at `src/orchestrator/agent_invoker.py` (NOT `src/deep_runtime/`).

### The deep seam + current middleware chain
- **Seam = `agent_invoker.py:396`** `if self._settings.runtime == "deep":` (deep branch `:396-449`; legacy `agent_loop` below). `thread_id` minted `:403`; `authorization_source=AuthorizationSource.DIRECT_USER_REQUEST` `:410` (so `trust_gate` short-circuits — the deep gate is dormant on direct chat). `resume_deep_turn:523-613`.
- Deep build helper `_build_deep_agent_for` (`:194-363`) returns `build_deep_agent(agent, shells, workspace_id=…, db_factory=…, extra_middleware=(governor_audit, trust_gate, write_lock, dispatcher, librarian_extract), system_prompt=…, checkpointer=self._checkpointer_provider() or MemorySaver())` (`:349-363`). Chain (outer→inner) = **`capability_scope → governor_audit → trust_gate → write_lock → dispatcher`** (`capability_scope` prepended by `build_deep_agent`; `librarian_extract` is an `@after_model` hook, chain-position-irrelevant).
- **Shared 7B1 fold** `_resolve_tool_def_shared` (`:222-229`) — a per-turn memoized `dict[str,tuple[bool,Any]]`; consumers: `governor_audit` (`:237`, fail-open), `trust_gate._gate_cap` (`:263-265`, fail-CLOSED), `write_lock._resolve_cap` (`:289-291`, fail-open). Per-turn closure — a separately-built child gets its own cache (relevant: each child delegate compiles its own gate chain).
- **6C redis carry-fix (landed `80825fb`)** at TWO sites: `_assess_risk` `:253` and `write_lock` `:298` read `self._services.extras.get("redis") if self._services else None`. `librarian_extract`'s `_librarian_learn` closure `:321-323` INTENTIONALLY keeps `getattr(self._services,"redis",None)`→None (byte-identical to live `InteractionLearner` `jarvis.py:172`). **Do NOT "fix" the librarian one.** Any NEW cheap-model cache (the critique) MUST use `self._services.extras.get("redis")`.
- `build_system_prompt(agent, context, capability_summary)` `:142-168` — SHARED legacy+deep (called before the seam). `_augment_system_blocks_for_inline` `:61-81` (Presenter-voice, 7B1 P4) applied at `:415-419` in the deep branch — **agent-agnostic; must NOT apply to research delegates** (they don't produce user replies; already flagged as a Step-10 lead-scoping gate).

### `build_deep_agent` (the factory to extend)
- `agent_builder.py:56-66` signature — params `(agent: SubAgent, tools: list[Any], *, workspace_id="", db_factory=None, extra_middleware: Sequence[Any]=(), system_prompt: str|SystemMessage|None=None, name: str|None=None, checkpointer=None) -> CompiledStateGraph`. **NO `subagents=` param today.**
- `:97-106` installs `make_capability_scope_middleware(agent=agent, workspace_id=…, db_factory=…)` first (when `db_factory` given), then `extend(extra_middleware)`. `:108-118` fail-closed `ValueError` if a write-capable agent would have no scope guard. `:120-127` calls `create_deep_agent(model=build_chat_model(agent), tools=tools, system_prompt=…, middleware=middleware, name=…, checkpointer=…)`. **Where `subagents=` gets added** (see Phase 1).

### deepagents 0.6.11 subagent API (installed, re-verified)
- `create_deep_agent(...)` accepts **`subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None`** at `graph.py:242`.
- **`SubAgent` TypedDict** `middleware/subagents.py:36-161`: required `name`/`description`/`system_prompt`; optional (`NotRequired`) `tools` (`:92`), `model: str|BaseChatModel` (`:98`), `middleware: list[AgentMiddleware]` (`:104`), `interrupt_on` (`:107`), `skills`, `permissions`, `response_format: …` (`:124` — structured output). (Prior memory transposed `model`/`tools` lines — actual `tools=92`, `model=98`.)
- **`CompiledSubAgent` TypedDict** = `{name, description, runnable: Runnable}`. **A `build_deep_agent(...)` result (a `CompiledStateGraph`) IS a `Runnable` → droppable directly as a `CompiledSubAgent`.** This bakes the child's gate into its own compiled graph (the lower-risk path vs. relying on deepagents re-assembling child middleware).
- **Isolation (Fork-2, load-bearing):** each subagent compiles into its OWN graph via `create_sub_agent` `middleware/subagents.py:459-511`; a raw `SubAgent`'s child middleware is assembled at `graph.py:619-640` (`spec.get("middleware",[])` extended at `:632`); the PARENT's middleware is NEVER passed to children. ⇒ a child's gate MUST live in its own spec/compiled graph.
- **`task` tool** `_build_task_tool` `middleware/subagents.py:528-731`. Exists iff ≥1 sync subagent present. Runs the child via `subagent.invoke`/`ainvoke` (`:668-694`/`:696-722`) — **blocking single-shot, NOT `.astream`**. Returns via `_return_command_with_state_update` `:600-638`: **a `Command(update={..., "messages":[ToolMessage(content, tool_call_id=…)]})`** (`:633-637`); `content` = `structured_response.model_dump_json()` if `response_format` set (`:612-619`) ELSE last non-empty `AIMessage.text` (`:625-631`). Child sees only the `task` `description` string, NOT parent history.
- **Disabling the ambient general-purpose (GP) subagent:** logic at `graph.py:693-694` — GP is auto-added UNLESS `general_purpose_subagent.enabled is False` OR a subagent already named `"general-purpose"` is present (`:695-748` builds it). Controlled ONLY via a registered `HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False))` in the process-global registry `_HARNESS_PROFILES` `harness_profiles.py:935`; `GeneralPurposeSubagentProfile.enabled: bool|None=None` `harness_profiles.py:97`. **Profile key** derives from provider (`"anthropic"`) + model_name (build_deep_agent passes a pre-built `ChatAnthropic`, not a string) → `"anthropic:<model_name>"` or provider-wide `"anthropic"`; registration is process-wide (a provider-wide disable affects EVERY anthropic `create_deep_agent` in the process). `task` STILL exists for custom named subagents when GP is disabled (custom specs appended `:686` before the GP block). `SubAgentMiddleware ∈ _REQUIRED_MIDDLEWARE` `graph.py:206-209` (can't be stripped, but is conditional on ≥1 subagent).
- **`task ∈ DEEPAGENTS_BUILTIN_NAMES`** `builtins.py:18-30` (`:28`) → every current gate (capability_scope `:100`, dispatcher `:57`, governor_audit `:64`) FALLS THROUGH for `task`. TODAY every deep agent carries an ungated GP `task` (7B1 note: "hits the tripwire-shell if invoked, bounded blast radius"). **The "disable task/GP" seam does NOT exist yet** — net-new 7B2 work.

### Streaming (the crux)
- `stream_adapter.py:167` `stream_mode=["messages","updates"]`; `:172` `agent.astream(...)` — **NO `subgraphs=True`** (grep-confirmed zero hits). `text_delta`/`thinking` from `messages` `AIMessageChunk` (`:175-187`), stamped with the ONE `agent_name` passed in (no origin discrimination); `tool_result` from `messages` `ToolMessage` (`:188-199`), `blocked ← status=="error"` (`:197`); `tool_call` from `updates` (`:200-226`); `approval_needed` from `"__interrupt__" in payload` (`:201-207`); `agent_done` synthesized (`:257-276`).
- **Consequence (Pass-2 crux):** a child run via `.ainvoke` under propagated parent callbacks, with `subgraphs=False` → the child's INTERNAL tool_calls do NOT stream (no subgraphs); the child's FINAL result arrives as the parent-level `task` ToolMessage → ONE `tool_result` frame `tool="task"`; the child's TOKEN DELTAS most-probably interleave into the parent `messages` stream as parent-attributed `text_delta` → **double-emission risk** (child reply text streamed AND folded into the `task` result) + mis-attribution. UNPROVEN — **Phase 0.2 spikes it.**

### Perceiver config + model factory (per-child model)
- Perceiver def `agents.py:29-76` — `capability_scope` is ALL reads + internal observation/cursor/world-model reads, **zero external-write (approval-required) caps** ⇒ read-only. `AGENT_MODEL_TIERS` `:16-23` (perceiver=sonnet); `AGENT_THINKING` `:182-189` (perceiver=6144). `SubAgent` dataclass `:192-214` (`name, prompt, model_tier, capability_scope, max_tokens, temperature, thinking`). `create_sub_agents()` `:217-230` sets `thinking=AGENT_THINKING.get(name, ThinkingConfig())` — the in-memory `AGENTS` singleton carries per-agent thinking.
- `model_factory.build_chat_model(agent: SubAgent) -> ChatAnthropic` `:28`; `MODEL_TIER_IDS` `:21-25` (`opus=claude-opus-4-8`, `sonnet=claude-sonnet-4-6`, `haiku=claude-haiku-4-5-20251001`); `ChatAnthropic` subclasses `BaseChatModel` → passable as `SubAgent["model"]`. Thinking budget derived from `agent.thinking` (`:48-59`).
- **REUSE-SEAM TRAP:** `AgentRegistry.load_as_sub_agents` `agent_registry.py:182-195` (the only DB-row→Jarvis-SubAgent helper) **DROPS `thinking`** (the `Agent` DB model has no `thinking` column). ⇒ **source the Perceiver delegate from the in-memory `create_sub_agents()`/`AGENTS` singleton** (thinking preserved), NOT the DB loader. No new DB row, no migration.

### Per-child gate pieces (reuse)
- `make_capability_scope_middleware(*, agent: SubAgent, workspace_id, db_factory)` `middleware/capability_scope.py:73-122` — closure-binds the agent (gates THAT child), fail-closed `_is_in_scope:35-70`, exempts builtins `:100`. **Must be built PER CHILD.**
- `make_jarvis_tool_dispatcher(*, execute_tool: ExecuteToolFn, user_id, workspace_id)` `middleware/jarvis_tool_dispatcher.py:32-78`; `ExecuteToolFn` sig `(name, args, user_id, workspace_id)->dict` `:29`; normalizes `{error|blocked}`→`ToolMessage(status="error")`, success `:75`. user_id/workspace_id from closure.
- `build_tool_shells(tool_defs: list[dict]) -> list[StructuredTool]` `tool_bridge.py:18-37` — inert tripwire shells; a per-child tool list built identically from the child's resolved tool dicts.
- **The full gated builder already exists: `_build_deep_agent_for` `agent_invoker.py:194-363`** — the exact routine to compile a gated child into a `CompiledStateGraph` (for a `CompiledSubAgent`). A read-only child needs only `capability_scope + dispatcher` (trust_gate/write_lock are inert for read-only, governor_audit optional).

### Governor critique seam
- `make_governor_audit_middleware` `governor_audit.py:38-98` (7B1, DORMANT) — a per-tool-call audit hook that PASSES `task` through. The critique is a per-delegate-SUMMARY hook — a NEW middleware that SITS BESIDE it (do not extend it).
- `task` returns a **`Command`** (`subagents.py:633-638`); summary at `result.update["messages"][0].content`. **No existing middleware inspects a task result / a Command return** — untrodden.
- **LLM-side-call template = RiskAssessor** `risk_assessor.py:74-121` `assess_risk` (Haiku, `max_tokens`, `parse_llm_json`, Pydantic, **fail-closed-to-high** on exception), `get_or_assess_risk:124-156` (Redis 24h cache, key `risk:{ws}:{sha256(...)[:24]}`). Already consumed dormant in `trust_gate`'s `assess_risk` closure `agent_invoker.py:240-260`. **Do NOT reuse the Governor SERVICE** `services/governor.py:53-332` (plan-shaped — creates `TaskRun`/`Approval`).
- **Annotation channel:** `ToolMessage.status` is binary (`success`/`error`; the SSE mapping `jarvis_tool_dispatcher.py:9` keys `blocked` off `status=="error"`) — a 3rd "unreviewed" state CANNOT use `status`. Use a key inside the ToolMessage **content JSON** (`"unreviewed": true` / `"critique": {...}`). Keep the degradation rule consistent with read-back (`readback.py:11-13`: a failed critique = "unreviewed" annotation, NEVER a block, for reads).
- **Decoupling from `ReadBackVerifier` (Fork-4):** `readback.verify_step:39-84` is self-contained, runs ONLY on the autonomous graph path for IRREVERSIBLE writes; a read-only delegate triggers it ZERO times. Keeping the critique code-decoupled is structurally free.

### Offline-spike precedent (reuse for Phase 0)
- Scripted streaming fake `BaseChatModel`: `tests/deep_runtime/test_trust_gate.py:109-149`, `tests/test_deep_gate_end_to_end.py:90-133`, `spikes/deep_stream/probe.py:91-211` (`ScriptedFakeChatModel`; Section 3 `:413-518` = the gated-denial template proving a `wrap_tool_call` guard emits `ToolMessage(status="error")` offline). `spikes/deep_collapse/inline_format_probe.py:77-135` streams a reply through the real adapter. `resolve_model` returns a pre-built `BaseChatModel` unchanged → a fake lead + a fake child model drive `task`→custom-subagent fully offline, no API key.

---

## 2. Scope

**7B2 IS** (all deep-runtime, behind `JARVIS_RUNTIME=deep` AND `deep_delegates_enabled=False`, dormant on default `legacy`/flag-off, chat byte-neutral):
- (Phase 0) SPIKE-FIRST offline proofs of the UNPROVEN assumptions: (0.1) a read-only child registered via `subagents=` runs GATED through `task` offline (its scope guard + dispatcher fire); (0.2) child streaming characterization through `stream_deep_agent_events` (double-emission/interleave) + the mitigation decision; (0.3) disabling the ambient GP `task` child; (0.4) a lead-side `@wrap_tool_call` on `task` reads + annotates the delegate summary `Command` offline.
- (Phase 1) `build_deep_agent(subagents=…)` plumbing + `deep_delegates_enabled` settings flag.
- (Phase 2) A read-only delegate builder — `build_read_only_delegate(...)` producing a `CompiledSubAgent` (or gated `SubAgent`, per the Phase-0 decision) from the in-memory Perceiver config, with per-child model + per-child `capability_scope`+`dispatcher`, `response_format` for a clean structured summary.
- (Phase 3) Disable the GP `task` child (HarnessProfile registration), gated on the flag.
- (Phase 4) Wire `subagents=[perceiver_delegate]` into the deep seam when the flag is on; bypass the Presenter-voice augmentation for delegates. DORMANT (flag-off default = byte-identical to 7B1).
- (Phase 5) Governor LLM delegate-critique middleware (`make_governor_delegate_critique_middleware`, `@wrap_tool_call` on `task`, Haiku, fail-open-annotated reads / fail-closed writes, redis-cached via `extras`). DORMANT.
- (Phase 6) Forced-on offline e2e guard: flag-on → lead delegates to Perceiver → child runs gated → GP disabled → critique annotates. Negative controls with TEETH.
- (Phase 7) Holistic opus + full gate + `__all__` hygiene + doc policy (NO CLAUDE.md edit — dormant deep internals aren't durable arch facts until MERGE).

**7B2 IS NOT:**
- A live lead→delegate routing decision (which agent delegates, when) — the single-lead concept = **Step 8/10**.
- Any change to the perception-path Perceiver (`call_agent`, always legacy) — untouched until Step 10.
- Any runtime flip, any CLAUDE.md durable edit, any agent-count reduction/migration (Perceiver reused, not removed).
- Inline read-back (7C), wiring `budget`/`unavailable_server` (7C), write-capable delegates.
- Fixing the 6C write-lock fail-open (#2) / contended-blocked shape (#3) — Step-10 activation gates.

---

## 3. File structure

**Create:**
- `backend/spikes/deep_delegate/subagent_gated_probe.py` — Phase 0.1/0.2/0.3 offline probe (real `create_deep_agent(subagents=…)`, scripted fake lead + child, `task` call streamed through the adapter; asserts child gate fires, characterizes streaming, proves GP disabled).
- `backend/spikes/deep_delegate/critique_probe.py` — Phase 0.4 offline probe (lead-side `@wrap_tool_call` on `task` reads the `Command` summary + annotates; fake critique model).
- `docs/superpowers/spikes/2026-07-08-deep-delegate-subagents.md` — spike decision doc (the streaming mitigation + SubAgent-dict-vs-CompiledSubAgent decision).
- `backend/src/deep_runtime/delegates.py` — `build_read_only_delegate(...)` + `DELEGATE_RESPONSE_FORMAT` (structured summary schema mirroring the Perceiver contract) + `disable_general_purpose_subagent(...)` HarnessProfile helper.
- `backend/src/deep_runtime/middleware/governor_delegate_critique.py` — `make_governor_delegate_critique_middleware(...)`.
- `backend/tests/deep_runtime/test_delegate_builder.py`, `test_governor_delegate_critique.py`, `test_delegate_e2e.py` (Phase 6 forced-on guard).

**Modify:**
- `backend/src/deep_runtime/agent_builder.py` — add `subagents=` param to `build_deep_agent`, forward to `create_deep_agent`.
- `backend/src/config/settings.py` — add `deep_delegates_enabled: bool = False`.
- `backend/src/orchestrator/agent_invoker.py` (HOT — sequence touches: Phase 4 seam, then Phase 5 critique wiring) — construct `subagents` when the flag is on; pass through `_build_deep_agent_for`; bypass `_augment_system_blocks_for_inline` for delegates; wire the critique middleware.
- `backend/src/deep_runtime/middleware/__init__.py` — add `governor_delegate_critique` to `__all__`.

**Leave untouched:** `perception_runner.py` (legacy Perceiver), `services/governor.py`, `governor_audit.py`, `chat_processor.py` (live activation deferred), `stream_adapter.py` UNLESS Phase 0.2 proves an adapter change is required (then it becomes a Phase-4b task with its own byte-neutral guard).

---

## 4. Design decisions (locks)

1. **CompiledSubAgent vs. gated-SubAgent-dict = Phase-0.1 DECIDES.** Both are viable (Pass-1/2/3). Default lean: **`CompiledSubAgent{runnable=build_read_only_delegate(...)}`** (gate baked into a self-compiled graph — no dependence on deepagents' child-middleware assembly). Fallback: a raw `SubAgent` dict carrying `middleware=[capability_scope, dispatcher]` + `model` + `response_format`. Phase 0.1 builds a read-only child BOTH ways and picks the one that (a) gates reliably offline (out-of-scope read denied, in-scope read dispatched — not the tripwire shell) and (b) yields a clean structured summary. The winner is recorded in the spike doc and used from Phase 2 on.
2. **Dormancy = a `deep_delegates_enabled` flag** (default `False`), gating BOTH the `subagents=` registration AND the GP-disable. Flag-off `deep` = byte-identical to 7B1. Never flipped in 7B2.
3. **Child middleware set = `capability_scope` + `dispatcher` ONLY** (read-only delegates have zero write caps ⇒ `trust_gate`/`write_lock` are inert no-ops; omit them for clarity + cost). `governor_audit` optional (audit-only). The child's `db_factory` MUST be passed so the scope guard installs.
4. **Per-child model = `build_chat_model(perceiver_config)`** from the in-memory `create_sub_agents()` singleton (thinking preserved: sonnet/6144). Passed as `SubAgent["model"]` or baked into the compiled child.
5. **GP disable = a HarnessProfile registered once** (Phase-0.3-decided key strategy — model-scoped `"anthropic:<model_name>"` preferred over provider-wide to avoid disabling GP for unrelated agents), gated on the flag. Phase 0.3 also evaluates the name-collision shortcut (include a `"general-purpose"`-named spec) as an alternative and picks the cleaner one.
6. **Critique = a NEW lead-side `@wrap_tool_call` middleware handling `task`** (the ONE middleware that does NOT skip it), OUTER of `SubAgentMiddleware` so `handler(request)` runs the delegate; reads `result.update["messages"][0].content`; runs a Haiku critique (clone RiskAssessor: `client.messages.create`, `parse_llm_json`, redis-cached via `self._services.extras.get("redis")`); read-only delegate (whole `capability_scope` all-reads) ⇒ **fail-open-ANNOTATED** (`"unreviewed": true` + `"critique": {...}` merged into the content JSON, NEVER block); write delegate ⇒ fail-closed (defensive guard, unreached in 7B2). Read/write classification is DELEGATE-level (whole scope), not call-level.
7. **Delegate prompts bypass `_augment_system_blocks_for_inline`** (Presenter-voice is for the lead reply, not research delegates).

---

## Phase 0 — SPIKE-FIRST (the unproven-offline gate)

**Rationale:** 6A/6B/6C/7B1-P0 each had a Task-0 spike that could DISPROVE the plan's central assumption. Here the assumptions are: (a) a `subagents=`-registered child with its OWN middleware actually gates when invoked through `task` offline; (b) the child's streamed output through the frozen adapter does not silently corrupt the SSE contract; (c) the GP child can be disabled; (d) a lead-side `@wrap_tool_call` can read + annotate the `task` `Command` result. If any fails, the plan changes before code is written.

### Task 0.1: Prove a read-only child runs GATED through `task`, offline

**Files:** Create `backend/spikes/deep_delegate/subagent_gated_probe.py`

- [ ] **Step 1 — Write the probe.** Reuse the `ScriptedFakeChatModel` pattern (`spikes/deep_stream/probe.py:91-211`). Two fakes: a LEAD fake scripted to emit ONE `task` tool-call chunk (`tool_call_chunk(name="task", args=json.dumps({"subagent_type":"researcher","description":"look up X"}), id="tc1", index=0)`) then a terminal text turn; a CHILD fake scripted to call an in-scope read tool (e.g. `internal.search`) then answer. Build the child BOTH ways:
  - (A) `CompiledSubAgent{name:"researcher", description:…, runnable: await build_deep_agent(perceiver_cfg, child_shells, workspace_id=WS, db_factory=<real-ish or fake>, extra_middleware=(child_dispatcher,), system_prompt=…)}` — where the dispatcher's `execute_tool` records calls and returns a fake read result, and the child's `capability_scope` is Perceiver's.
  - (B) raw `SubAgent{name, description, system_prompt, model:<child fake>, tools:child_shells, middleware:[child_capability_scope, child_dispatcher], response_format:DELEGATE_RESPONSE_FORMAT}`.
  Register via `create_deep_agent(model=<lead fake>, tools=lead_shells, subagents=[child], middleware=[...])`. Drive `stream_deep_agent_events(agent, ...)`.
- [ ] **Step 2 — Assert the GATE fires (both build methods).** The child's in-scope read reaches the CHILD dispatcher's `execute_tool` (recorded), NOT the tripwire shell (no `error` frame from a shell); an OUT-of-scope tool (add a second scripted child call to a cap not in Perceiver's scope) is DENIED by the child's `capability_scope` → `ToolMessage(status="error")`. **Negative control:** remove the child's `capability_scope` from the spec → the out-of-scope call is NO LONGER denied (proves the child gate, not the parent, is doing the work).
- [ ] **Step 3 — Run offline** (no API key): `uv run python spikes/deep_delegate/subagent_gated_probe.py`. Record which build method (A/B) gates cleanly + returns a structured summary. **Decision → spike doc.**

### Task 0.2: Characterize child STREAMING through the adapter

- [ ] **Step 1 — Extend the probe** to assert on the frames from `stream_deep_agent_events`: does the child's answer appear as (a) a `tool_result` frame `tool="task"` (expected, deterministic), and/or (b) parent-attributed `text_delta` frames (the interleave risk)? Count `text_delta` frames and check whether any carry the CHILD's tokens.
- [ ] **Step 2 — Test the mitigations** in order until one gives a clean contract: (i) child uses `response_format=DELEGATE_RESPONSE_FORMAT` (structured → the `task` content is JSON, no free-text child reply to leak); (ii) if child text still interleaves, try `subgraphs=True` on the parent `astream` + adapter namespace attribution (a Phase-4b adapter change, byte-neutral-guarded); (iii) if neither, the delegate's summary is consumed only by the critique/lead and child `text_delta` is suppressed by filtering on langgraph node metadata. **Decision → spike doc**, with the concrete adapter delta (if any) specified for Phase 4b.
- [ ] **Step 3 — Record** whether the frozen 8-frame SSE contract survives unchanged (the 7B2 goal) or needs a documented, guarded adapter change.

### Task 0.3: Prove the ambient GP `task` child can be DISABLED

- [ ] **Step 1 — In the probe**, register `disable_general_purpose_subagent(model_name)` (a HarnessProfile with `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)`, keyed `"anthropic:<model_name>"`) BEFORE `create_deep_agent`. Compile an agent with `subagents=[researcher]` and assert (via `compiled.nodes["tools"].bound.tools_by_name` / the subagent registry) that a `"general-purpose"` subagent is NOT present but `task` still routes to `"researcher"`. Also test the name-collision alternative (a `"general-purpose"`-named custom spec).
- [ ] **Step 2 — Negative control:** without the disable, assert a `"general-purpose"` child IS present (proves the disable is doing the work). Record the key-scoping proof (a second agent of a DIFFERENT model is NOT affected). **Decision → spike doc.**

### Task 0.4: Prove a lead-side `@wrap_tool_call` reads + annotates the `task` `Command`

**Files:** Create `backend/spikes/deep_delegate/critique_probe.py`

- [ ] **Step 1 — Write a minimal `@wrap_tool_call` middleware** that, for `name=="task"`, does `result = await handler(request)` then reads `result.update["messages"][0].content`, and merges an `"unreviewed": true` key into the content JSON (rebuild the `Command`/`ToolMessage`). Register it OUTER in `middleware=[critique, ...]` on a real `create_deep_agent(subagents=[researcher])` with fake models.
- [ ] **Step 2 — Assert** the returned `task` `tool_result` frame's content carries `"unreviewed": true` (the annotation survives to the adapter). Confirm the middleware is the ONE that does NOT skip `task` and that placement (outer of `SubAgentMiddleware`) lets `handler` run the delegate. **Negative control:** a middleware that skips `task` (builtin exemption) → no annotation. **Decision → spike doc** (Command-unwrapping recipe + placement).

- [ ] **Task 0.5 — Commit spikes + decision doc.** `git add backend/spikes/deep_delegate/ docs/superpowers/spikes/2026-07-08-deep-delegate-subagents.md && git commit -m "spike(rebuild): deep-delegate subagents gate/stream/GP-disable/critique offline proofs (Step 7B2 P0)"`. **If any spike DISPROVES an assumption, STOP and revise this plan before Phase 1.**

---

## Phase 1 — `build_deep_agent(subagents=)` plumbing + flag

### Task 1: `deep_delegates_enabled` settings flag

**Files:** Modify `backend/src/config/settings.py`; Test `backend/tests/test_settings.py` (or the deep-runtime settings test)

- [ ] **Step 1 — Failing test:** assert `get_settings().deep_delegates_enabled is False` by default and that `JARVIS_DEEP_DELEGATES_ENABLED=true` env sets it True.
- [ ] **Step 2 — Run → FAIL** (attribute missing).
- [ ] **Step 3 — Add** `deep_delegates_enabled: bool = False` next to `runtime` in `settings.py` (mirror the `JARVIS_`-prefixed pydantic-settings pattern).
- [ ] **Step 4 — Run → PASS.**
- [ ] **Step 5 — Full gate** `uv run pytest tests/ --ignore=tests/e2e` → 3260+ passed. **Commit** `feat(rebuild): deep_delegates_enabled flag, off-by-default (Step 7B2 P1)`.

### Task 2: `build_deep_agent(subagents=)` forwarding

**Files:** Modify `backend/src/deep_runtime/agent_builder.py:56-127`; Test `backend/tests/deep_runtime/test_agent_builder.py`

- [ ] **Step 1 — Failing test:** `build_deep_agent(agent, tools, subagents=[<a CompiledSubAgent dict>])` compiles a graph whose `task` routes to the registered subagent; `subagents=()` (default) is byte-identical to today (no `subagents=` reaches `create_deep_agent`). Use a fake `CompiledSubAgent{name,description,runnable=<trivial compiled graph>}`.
- [ ] **Step 2 — Run → FAIL** (unexpected keyword `subagents`).
- [ ] **Step 3 — Add** `subagents: Sequence[Any] = ()` to the signature (`:56-66`) and forward `subagents=subagents or None` into the `create_deep_agent(...)` call (`:120-127`). Default `()`→`None` preserves today's call exactly.
- [ ] **Step 4 — Run → PASS.** Assert the fail-closed write-capability guard (`:108-118`) is unaffected (still inspects only the LEAD agent).
- [ ] **Step 5 — Full gate. Commit** `feat(rebuild): build_deep_agent forwards subagents= to create_deep_agent (Step 7B2 P1)`.

---

## Phase 2 — read-only delegate builder (Perceiver-as-delegate)

**Files:** Create `backend/src/deep_runtime/delegates.py`; Test `backend/tests/deep_runtime/test_delegate_builder.py`

### Task 3: `DELEGATE_RESPONSE_FORMAT` + `build_read_only_delegate(...)`

- [ ] **Step 1 — Failing test:** `build_read_only_delegate(agent_config=perceiver_cfg, *, workspace_id, user_id, db_factory, execute_tool)` returns a `CompiledSubAgent` (per Phase-0.1 decision) with `name==perceiver_cfg.name`, whose compiled child (a) uses the sonnet model (`build_chat_model(perceiver_cfg)` → model_name contains `claude-sonnet-4-6`), (b) has a `capability_scope` guard bound to Perceiver's scope, (c) a dispatcher whose `execute_tool` is the injected one, (d) NO trust_gate/write_lock (read-only). Assert an out-of-scope tool call is denied (mutation-proof the child gate).
- [ ] **Step 2 — Run → FAIL.**
- [ ] **Step 3 — Implement.** `DELEGATE_RESPONSE_FORMAT` = a schema mirroring the Perceiver contract (`prompts.py:384-399`: `findings[]`, `synthesis`, `gaps`). `build_read_only_delegate` sources the Perceiver `SubAgent` from the in-memory `create_sub_agents()` singleton (thinking preserved — NOT `load_as_sub_agents`), builds child tool shells from Perceiver's resolved read tools, a per-child `make_capability_scope_middleware(agent=perceiver_cfg, …)` + `make_jarvis_tool_dispatcher(execute_tool=…, user_id, workspace_id)`, compiles via `build_deep_agent(perceiver_cfg, child_shells, workspace_id=…, db_factory=…, extra_middleware=(dispatcher,), system_prompt=build_system_prompt(perceiver_cfg,""))`, and wraps it `CompiledSubAgent(name=…, description=…, runnable=<compiled>)`. **Assert the delegate prompt does NOT include the Presenter-voice augmentation.**
- [ ] **Step 4 — Run → PASS.**
- [ ] **Step 5 — Full gate. Commit** `feat(rebuild): read-only Perceiver delegate builder + structured summary format (Step 7B2 P2)`.

---

## Phase 3 — disable the ambient GP `task` child

**Files:** `backend/src/deep_runtime/delegates.py` (add `disable_general_purpose_subagent`); Test `test_delegate_builder.py`

### Task 4: GP-disable helper (Phase-0.3 decision)

- [ ] **Step 1 — Failing test:** after `disable_general_purpose_subagent(model_name)` (or the decided approach), a `build_deep_agent(perceiver_cfg, tools, subagents=[delegate])` compiles WITHOUT a `"general-purpose"` subagent, but `task` still exists and routes to the delegate. A DIFFERENT-model agent is unaffected (key scoping). **Negative control:** without the call, `"general-purpose"` IS present.
- [ ] **Step 2 — Run → FAIL.**
- [ ] **Step 3 — Implement** per the Phase-0.3 decision (HarnessProfile registered in `_HARNESS_PROFILES` keyed `"anthropic:<model_name>"`, idempotent registration; OR the name-collision shortcut). Idempotent (safe to call every turn).
- [ ] **Step 4 — Run → PASS.**
- [ ] **Step 5 — Full gate. Commit** `feat(rebuild): disable ambient general-purpose task subagent on deep delegate lead (Step 7B2 P3)`.

---

## Phase 4 — wire delegates into the deep seam (DORMANT)

**Files:** Modify `backend/src/orchestrator/agent_invoker.py` (HOT — Phase-4 touch #1 of 2); Test `backend/tests/deep_runtime/test_agent_invoker_delegates.py`. **(Phase 4b only if Phase 0.2 required an adapter change — then modify `stream_adapter.py` with a byte-neutral guard.)**

### Task 5 (BLAST-RADIUS → 2-stage PARALLEL spec+quality review on the frozen commit)

- [ ] **Step 1 — Failing test:** in `call_agent_stream`, when `runtime=="deep"` AND `deep_delegates_enabled=True`, `_build_deep_agent_for` receives a non-empty `subagents=[perceiver_delegate]` (and GP-disable is applied); when the flag is False, `subagents` is empty and the deep build is **byte-identical to 7B1** (assert the `build_deep_agent` call args are unchanged flag-off). Delegates bypass `_augment_system_blocks_for_inline`.
- [ ] **Step 2 — Run → FAIL.**
- [ ] **Step 3 — Implement.** In `_build_deep_agent_for` (`:194-363`): when the flag is on, construct `subagents=[build_read_only_delegate(perceiver_cfg, workspace_id=…, user_id=…, db_factory=self._db_factory, execute_tool=…)]` (reuse the SAME `execute_tool`/`workspace_id`/`user_id`/redis-from-`extras` deps the lead uses) + call `disable_general_purpose_subagent(...)`, and thread `subagents=` into the `build_deep_agent(...)` call (`:349-363`). Flag-off → `subagents=()` (unchanged). Ensure the Presenter-voice augmentation (`:415-419`) is NOT applied to the delegate's own prompt (it already isn't — the delegate builds its own prompt; assert it).
- [ ] **Step 4 — Run → PASS.** Confirm flag-off deep tests (`test_agent_invoker_deep_hardening.py`, the 7B1 suite) still pass byte-identically.
- [ ] **Step 5 — Full gate. Commit** `feat(rebuild): wire read-only Perceiver delegate into deep seam, flag-gated dormant (Step 7B2 P4)`.
- [ ] **Step 6 — 2-stage PARALLEL review** on the frozen commit: Reviewer A (spec) = dormancy/flag-off byte-identity + delegate wiring; Reviewer B (quality) = closure-dep correctness (redis from `extras`, user_id/workspace_id per child), no cross-tenant/leak, `_augment` bypass. Address findings.

---

## Phase 5 — Governor LLM delegate-critique middleware (DORMANT)

**Files:** Create `backend/src/deep_runtime/middleware/governor_delegate_critique.py`; Modify `agent_invoker.py` (HOT — Phase-5 touch #2 of 2, AFTER Phase 4), `middleware/__init__.py`; Test `backend/tests/deep_runtime/test_governor_delegate_critique.py`

### Task 6: the critique middleware (independent opus review — load-bearing)

- [ ] **Step 1 — Failing test (offline, fake critique model):** `make_governor_delegate_critique_middleware(*, critique_model, redis, is_read_only_delegate)` returns a `@wrap_tool_call` middleware that, for `name=="task"`: runs `handler`, reads `result.update["messages"][0].content`, calls the (fake) Haiku critique over the summary, and for a READ-ONLY delegate merges `"unreviewed": <bool>` + `"critique": {...}` into the content JSON, NEVER blocking. Assert: (a) a clean summary → `"unreviewed": false`; (b) a critique-flagged summary → `"unreviewed": true` + reasons, result STILL returned (fail-open); (c) a critique MODEL EXCEPTION → fail-open-annotated `"unreviewed": true` (read) — never a block; (d) `name!="task"` → passthrough (does NOT skip other tools' real gates). **Negative control:** force the write-delegate branch (`is_read_only_delegate=False`) + a flagged critique → the tool_result is `status="error"`/blocked (fail-closed) — proves the read/write branch is real.
- [ ] **Step 2 — Run → FAIL.**
- [ ] **Step 3 — Implement.** Clone the RiskAssessor side-call shape (`risk_assessor.py:74-121`): `critique_model.messages.create(...)` (or a `client` + `get_haiku_model()`), `parse_llm_json`, Pydantic-validate a `CritiqueVerdict{ok: bool, concerns: list[str]}`; redis cache OPTIONAL (via injected `redis`), keyed on `sha256(summary)[:24]`. Read/write from the injected `is_read_only_delegate` (delegate-level). Command-unwrap + re-wrap per the Phase-0.4 recipe. Do NOT import `services/governor.py`.
- [ ] **Step 4 — Run → PASS.**
- [ ] **Step 5 — Wire (agent_invoker, flag-gated):** build the critique middleware in `_build_deep_agent_for` ONLY when `deep_delegates_enabled` (delegates exist to critique), OUTER of `SubAgentMiddleware` (prepend in `extra_middleware` or place so it wraps `task`), `redis=self._services.extras.get("redis") if self._services else None`, `critique_model=<Haiku via model_factory or self._client>`, `is_read_only_delegate=True` (Perceiver). Add `governor_delegate_critique` to `middleware/__init__.py __all__`.
- [ ] **Step 6 — Full gate. Commit** `feat(rebuild): Governor LLM delegate-summary critique middleware, dormant (Step 7B2 P5)`. **Independent opus review** (load-bearing): degradation modes (fail-open reads / fail-closed writes), redis-from-extras, no Governor-service coupling, Command-unwrap correctness.

---

## Phase 6 — forced-on offline e2e guard

**Files:** Create `backend/tests/deep_runtime/test_delegate_e2e.py`

### Task 7: the load-bearing guard (independent reproduction by the holistic)

- [ ] **Step 1 — Write** a forced-`deep_delegates_enabled=True` offline e2e (fake lead + fake Perceiver child + fake critique model), REAL `_build_deep_agent_for`/`build_deep_agent`/`stream_deep_agent_events`: lead emits a `task(subagent=perceiver)` call → the child runs its in-scope read via the dispatcher (recorded), returns a structured summary → the critique annotates it (`"unreviewed"` present) → the summary reaches the lead as the `task` `tool_result` → the GP `"general-purpose"` child is absent. Assert the frozen SSE frame set is intact (per Phase-0.2 decision) and no `error` frame from a tripwire shell.
- [ ] **Step 2 — Negative controls (reproduced by the holistic opus):** (a) remove the child `capability_scope` → an out-of-scope child read executes (gate gone) → FAIL; (b) remove the critique middleware → no `"unreviewed"` annotation → FAIL; (c) flag OFF → NO delegate, NO GP-disable, deep build byte-identical to 7B1 → the delegate assertions are vacuous/skip. Each negative control: revert-fix → watch-it-fail → `git checkout` restore, tree clean.
- [ ] **Step 3 — Full gate. Commit** `test(rebuild): forced-on deep-delegate e2e guard + negative controls (Step 7B2 P6)`.

---

## Phase 7 — holistic + hygiene

### Task 8: holistic opus + full gate + doc policy

- [ ] **Step 1 — Full gate** `uv run pytest tests/ --ignore=tests/e2e` → expect 3260 + new tests passed / 18 skipped / 0 failed. Verify the count yourself (18 skipped, not ~108).
- [ ] **Step 2 — Migration check:** `uv run alembic heads` → single `1a2770a28c39`; `uv run alembic check` → drift-free. Confirm ZERO migrations (no agent removed).
- [ ] **Step 3 — Ruff** `uv run ruff check src tests` → clean. `middleware/__init__.py __all__` includes `governor_delegate_critique`.
- [ ] **Step 4 — Holistic opus review:** re-run the full gate + alembic; independently REPRODUCE all Phase-6 negative controls; verify dormancy (flag-off deep + legacy both byte-neutral); verify redis-from-`extras` everywhere new; verify no live lead→delegate routing was introduced (grep the seam for an unconditional `subagents=`). = SHIP or fix-then-re-verify.
- [ ] **Step 5 — NO CLAUDE.md edit** (dormant deep internals are not durable arch facts until MERGE, per doc policy). **Commit** any holistic fixes.

---

## Review strategy

- Phase 0 spikes = combined review re-running the probe (could DISPROVE the plan — treat seriously).
- Phase 1/2/3 = combined single review (mechanical/additive).
- **Phase 4 (deep-seam wiring) = 2-stage PARALLEL spec+quality on the frozen commit** (blast-radius — the hot shared file).
- **Phase 5 (critique middleware) = independent opus review** (load-bearing degradation modes).
- Phase 6 = the negative-control guard; Phase 7 = final holistic opus (independently reproduces the negative controls).
- **Single-owner-per-file + SYNCHRONOUS implementer dispatch.** `agent_invoker.py` touched in Phase 4 then Phase 5 IN ORDER (never concurrently).

## Migrations

**Expect NONE.** No agent removed (Perceiver reused as a delegate, not a new row; no agent-count reduction). Head stays `1a2770a28c39`. **VERIFY** at Phase 7 (`alembic heads` + `alembic check`). If any task appears to need a migration, STOP — it means the delegate was mis-modeled (should be in-memory config, not a DB row).

## Activation gates (Step 8/10 — carried, NOT built here)

- (a) Flip `deep_delegates_enabled=True` + build the LIVE lead→delegate routing decision (the single-lead concept) — Step 8/10.
- (b) The perception-path Perceiver stays legacy until Step 10.
- (c) If Phase 0.2 required a `subgraphs=True` adapter change, re-audit child-frame attribution before live activation.
- (d) Custom (non-Perceiver) research delegates + write-capable delegates (would need trust_gate/write_lock in the child chain + a DB row/migration) — later.
- (e) The GP-disable HarnessProfile's process-global scope — re-audit that no legitimate GP use exists before live activation.
- (f) Carried 6C activation gates #2 (write-lock fail-open) / #3 (contended-blocked shape) unchanged.

## Out of scope (confirmed not built)

Live routing / single-lead (Step 8/10); perception Perceiver cutover (Step 10); inline read-back + `budget`/`unavailable_server` wiring (7C); write-capable delegates; CLAUDE.md durable edit; agent-count reduction/migration; runtime flip.
