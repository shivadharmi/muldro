# Engineering Standards

These rules are binding for all contributions — including AI-assisted ones. Each rule exists
because its absence caused a real problem in this codebase. When a rule blocks you, the rule
wins until changed here first.

## 1. Architecture

- **One-way dependencies.** `api → services → {models, contracts}`; `orchestrator → services`;
  `tools → services`. `contracts/` is the neutral boundary-contract layer (PlanOutput, PlanStep,
  SurfaceUpdate, StepResult, PolicyDecision, ...) that both api and services import downward from.
  Nothing imports back up the chain. If you need an upward call, you need an event or a callback
  injected from above — not an import.
- **No new code on god objects.** `MuldroOrchestrator` (`orchestrator/muldro.py`) and
  `GraphExecutor` (`services/graph_executor.py`) are frozen: new behavior goes into collaborator
  classes injected via constructor, never new methods on the hub.
- **Contracts at every boundary.** Anything crossing a process or layer boundary — API response,
  SSE event, agent output, surface payload, queue message — is a typed Pydantic model
  (frozen where possible, `Literal` discriminators for unions). Never a bare dict.
- **Discriminated unions over type-sniffing.** Replace `isinstance` chains and
  `event["type"] == "..."` string matching with Pydantic discriminated unions matched on type.
- **File size standard.** Target 200–400 lines per file; hard cap **800** (Python),
  **400** (React components), **200** (Zustand stores). Hitting a cap is a design signal —
  split by responsibility, not by line count. Enforced by pre-commit (`scripts/check_file_size.py`);
  pre-existing oversized files are grandfathered in the script's exemption list and must not grow —
  each one carries a standing debt to be split (`muldro.py`, `graph_executor.py`, etc.).
  Function cap: ~50 lines.
- **State changes only through transition functions.** Never mutate a status field directly;
  use `transition_run()` / `transition_step()` and extend the same pattern to any new state machine.

## 2. OOP & Design Patterns

**Use deliberately:**
- **Constructor dependency injection** — collaborators passed in `__init__`; no service locators,
  no module-level singletons except `settings`.
- **Strategy** — capability→agent resolution, model-tier selection.
- **Adapter** — thin entry points over a shared core (e.g., batch result folded from the
  streaming pipeline). Two public methods must not own two control flows.
- **Builder** — typed surface construction only (`ui/renderer.py`).
- **Protocol** (structural typing) over ABC inheritance for service interfaces.
- **Circuit breaker + retry-with-backoff** for every external call (API, MCP, datastore).

**Avoid:**
- Inheritance deeper than 2 levels; mixins for code-sharing; metaclasses; `__getattr__` indirection.
- Pattern theater: a pattern earns its place by deleting a known failure mode, not by existing.
  Prefer a frozen dataclass + functions over a class with one method.

## 3. Python

- Type hints on all signatures. No `Any` in public interfaces.
- Async everywhere; no blocking I/O inside async paths.
- **Immutability by default**: frozen Pydantic models / dataclasses; functions return new values
  rather than mutating arguments or shared state.
- Errors are never silently swallowed. Every `except` handles, enriches, or re-raises.
  `HTTPException` only at the API layer. Tool/agent error payloads carry explicit error flags.
- DB: `async with db_factory() as db:` + explicit `await db.commit()`. Schema changes via Alembic only.
- IDs: ULID with type prefix (`evt_`, `plan_`, `run_`, ...).
- Tooling: `ruff check` + `ruff format` (line length 100, target py312) must pass.

## 4. Frontend (Next.js / React)

- Hooks called unconditionally at the top; no side effects during render; lazy `useState`
  initializers for storage-derived state; `useRouter().replace()` in effects for redirects.
- Components target ~300 lines, below the enforced 400-line hard cap (§1); feature-folder
  organization; one Zustand store per concern.
- API client split per domain (`api/auth.ts`, `api/chat.ts`, ...) with typed responses
  mirroring backend Pydantic models.

## 5. Refactoring Process

- **Characterization tests before risky structural change** — freeze current behavior first;
  the refactor must keep them green except where a documented decision says otherwise.
- **One mechanical move per commit; tests green at every commit** — each commit independently
  revertable and bisectable.
- **Structure and behavior never change in the same commit.** Behavior changes get their own
  commit referencing the decision that authorized them.
- No drive-by refactors outside the declared scope of the change.

## 6. A2UI / Artifact Surfaces

- **The side-effect line is law:** UI that triggers actions (approve, execute, dismiss-with-
  consequences) is a typed, server-authored component wired through TrustEngine and typed
  contracts. Render-only content may be a generated HTML artifact. No action callbacks from
  artifact HTML — ever.
- Artifacts render exclusively inside a sandboxed iframe: strict CSP, no parent DOM access,
  no network, no storage. All interpolated content is escaped server-side.
- Artifact kinds are never delivered to text-only surfaces (Slack/email keep Markdown).
- Replaced code is deleted only after the replacement passes tests and a manual verification pass.

## 7. Open-Source Hygiene

- Secrets never appear in code, fixtures, examples, or docs. `.env` and credential files stay
  untracked; secret scanning runs in pre-commit and CI — enforced, not remembered.
- Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `perf:`, `ci:`).
  No attribution trailers.
- Every public module carries a docstring stating why it exists, not just what it does.
- New features ship with tests (80% coverage target). Structural changes update the relevant
  `docs/architecture/` page in the same PR.
- Once released, `/v1/` API routes are a public contract: breaking changes require versioning
  and a changelog entry.

## 8. Documentation Maintenance

**Code is the source of truth.** Docs capture durable architecture, design intent, and invariants — not an inventory of the codebase. They exist to record what the code cannot make obvious on its own: the *why*, the layering, the contracts, the constraints.

**Do not document volatile data.** Never write file counts, line counts, migration counts, router/model/test/tool counts, or any figure that changes with routine work. They rot within days and mislead agents into false precision. Name the directory or module and let the reader inspect it (`ls`, `grep`) for current specifics.

**Document the durable, not the incidental:**
- DO: layering and one-way deps, boundary contracts, invariants, state machines, the agent topology and roles, design decisions and their rationale, non-obvious constraints.
- DON'T: counts/inventories, line numbers as identity, exhaustive file lists, anything trivially re-derivable from the code.

**Update docs only when an architectural fact changes** — a component added/removed, a changed contract/invariant/dependency, a renamed concept. Routine edits (adding a file, tool, migration, or test) require **no** doc change. When in doubt, leave the docs: a smaller doc that is correct beats a larger one that drifts.

**Design constants vs. inventory.** Behavioral/design constants (matrix dimensions, decay rates, TTLs, thresholds, timeouts, named rosters) are meaningful and rarely change — keep them. Inventory counts (how many files/migrations/tests exist) are churn — never record them.

## 9. Enforcement

- **Pre-commit**: ruff check, ruff format, gitleaks secret scan (`.pre-commit-config.yaml`).
- **CI** (to be added with release packaging): pytest, ruff, frontend lint + build, secret scan.
- Coverage and TDD remain targets, not hard gates, until post-launch — except for new
  feature code, where tests are required at review.
