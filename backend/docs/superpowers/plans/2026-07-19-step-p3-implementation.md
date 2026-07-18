# P3 Implementation Plan — todos surface + `mode` → `permission_mode` + per-workspace default

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`)
> syntax for tracking.

**Goal:** Deliver the P3 UX layer of the chat permission model — an inline `write_todos` checklist,
a `mode`→`permission_mode` swap across API+frontend, and a per-workspace `permission_mode` default.

**Architecture:** Three sequential phases (P3a → P3b → P3c), each landing green + reviewed before the
next. P3a is frontend-only (the todos already stream in the `write_todos` `tool_call` frame). P3b
removes the user-facing legacy `mode` (keeping it internal for pinned callers) and moves the picker to
`auto`/`ask`/`bypass`. P3c stores a per-workspace default in the existing `Workspace.settings` JSONB
(no migration) and resolves it backend-authoritative at the interactive `routes_chat` handler only
(never `_process_core`, which pinned callers share).

**Tech Stack:** Backend — FastAPI, Pydantic, SQLAlchemy, pytest (custom asyncio hook; write `async def
test_*` directly). Frontend — Next.js/React, Zustand, Vitest + @testing-library/react + jsdom.

**Design doc:** `backend/docs/superpowers/plans/2026-07-19-step-p3-ux-permission-mode-design.md`.
**Grounding baseline:** HEAD `03bd913`; anchors below were verified against real code — **re-verify by
symbol name before editing** (10D shifts seams). Commit format: no backticks in `git commit -m` under
zsh — write the message to a scratch file and `git commit -F`. No `Co-Authored-By`. Pre-commit hooks
active. **No migrations. Not pushed/merged.** Full gate at every commit:
`cd backend && uv run pytest tests/ --ignore=tests/e2e -q` + `uv run ruff check src tests` +
`uv run ruff format --check src tests`; frontend `cd frontend && npm run lint && npm run test && npm run build`.

---

## Phase P3a — inline `write_todos` checklist (frontend-only)

**Files:**
- Create: `frontend/src/lib/todos.ts`, `frontend/src/lib/todos.test.ts`
- Create: `frontend/src/components/jarvis/chat-todos.tsx`, `frontend/src/components/jarvis/chat-todos.test.tsx`
- Modify: `frontend/src/components/jarvis/chat-panel.tsx` (`tool_call` case ~:409, `tool_result` case ~:429; assistant message model + render)

### Task 1: `todosFromToolCall` pure helper

**Files:** Create `frontend/src/lib/todos.ts` + `frontend/src/lib/todos.test.ts`

- [ ] **Step 1: Write the failing test** — `frontend/src/lib/todos.test.ts`

```typescript
import { test, expect } from "vitest";
import { todosFromToolCall, type Todo } from "./todos";
import type { ChatSSEEvent } from "./api";

test("returns todos from a write_todos tool_call", () => {
  const event = {
    event: "tool_call",
    tool: "write_todos",
    input: {
      todos: [
        { content: "Book flight", status: "in_progress" },
        { content: "Email John", status: "pending" },
      ],
    },
  } as unknown as ChatSSEEvent;
  expect(todosFromToolCall(event)).toEqual<Todo[]>([
    { content: "Book flight", status: "in_progress" },
    { content: "Email John", status: "pending" },
  ]);
});

test("returns null for a non-write_todos tool_call", () => {
  const event = { event: "tool_call", tool: "search_web", input: {} } as unknown as ChatSSEEvent;
  expect(todosFromToolCall(event)).toBeNull();
});

test("returns null for a non-tool_call event", () => {
  const event = { event: "text_delta", text: "hi" } as unknown as ChatSSEEvent;
  expect(todosFromToolCall(event)).toBeNull();
});

test("coerces an unknown status to pending and missing content to empty", () => {
  const event = {
    event: "tool_call",
    tool: "write_todos",
    input: { todos: [{ content: "x", status: "weird" }, {}] },
  } as unknown as ChatSSEEvent;
  const todos = todosFromToolCall(event);
  expect(todos?.[0].status).toBe("pending");
  expect(todos?.[1]).toEqual({ content: "", status: "pending" });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/todos.test.ts`
Expected: FAIL — `Failed to resolve import "./todos"`.

- [ ] **Step 3: Write minimal implementation** — `frontend/src/lib/todos.ts`

```typescript
import type { ChatSSEEvent } from "./api";

export type TodoStatus = "pending" | "in_progress" | "completed";

export interface Todo {
  content: string;
  status: TodoStatus;
}

function normalizeStatus(s: unknown): TodoStatus {
  return s === "in_progress" || s === "completed" ? s : "pending";
}

/** Extract the todos array from a deepagents `write_todos` tool_call event, else null.
 * `write_todos` rewrites the full list each call; the array rides in the tool_call
 * frame's `input.todos` (verified: stream_adapter.py emits `input: call.args`). Defensive
 * about item shape — re-verify field names against a live frame at build. */
export function todosFromToolCall(event: ChatSSEEvent): Todo[] | null {
  const e = event as { event?: string; tool?: string; input?: { todos?: unknown } };
  if (e.event !== "tool_call" || e.tool !== "write_todos") return null;
  const raw = e.input?.todos;
  if (!Array.isArray(raw)) return null;
  return raw
    .filter((t): t is Record<string, unknown> => typeof t === "object" && t !== null)
    .map((t) => ({ content: String(t.content ?? t.task ?? ""), status: normalizeStatus(t.status) }));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/todos.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/todos.ts frontend/src/lib/todos.test.ts
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3a — todosFromToolCall helper
```

### Task 2: `ChatTodos` presentational component

**Files:** Create `frontend/src/components/jarvis/chat-todos.tsx` + `.test.tsx`

- [ ] **Step 1: Write the failing test** — `frontend/src/components/jarvis/chat-todos.test.tsx`

```typescript
import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { ChatTodos } from "./chat-todos";

test("renders each todo's content", () => {
  render(
    <ChatTodos
      todos={[
        { content: "Book flight", status: "in_progress" },
        { content: "Email John", status: "completed" },
      ]}
    />,
  );
  expect(screen.getByText("Book flight")).toBeTruthy();
  expect(screen.getByText("Email John")).toBeTruthy();
});

test("shows the completed/total count", () => {
  render(<ChatTodos todos={[{ content: "a", status: "completed" }, { content: "b", status: "pending" }]} />);
  expect(screen.getByText(/1\s*\/\s*2/)).toBeTruthy();
});

test("renders nothing for an empty list", () => {
  const { container } = render(<ChatTodos todos={[]} />);
  expect(container.firstChild).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/jarvis/chat-todos.test.tsx`
Expected: FAIL — cannot resolve `./chat-todos`.

- [ ] **Step 3: Write minimal implementation** — `frontend/src/components/jarvis/chat-todos.tsx`

```typescript
import type { Todo, TodoStatus } from "@/lib/todos";

const GLYPH: Record<TodoStatus, string> = {
  pending: "○",
  in_progress: "◉",
  completed: "✓",
};

interface ChatTodosProps {
  todos: Todo[];
}

/** Claude-Code-style inline checklist for the lead's `write_todos` plan. Ephemeral per-turn;
 * rewritten in place on each `write_todos` call. */
export function ChatTodos({ todos }: ChatTodosProps) {
  if (todos.length === 0) return null;
  const done = todos.filter((t) => t.status === "completed").length;
  return (
    <div className="my-2 rounded-[var(--radius-md)] border border-b-secondary bg-surface-1 p-3">
      <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-2">
        Plan · {done}/{todos.length}
      </p>
      <ul className="space-y-1">
        {todos.map((t, i) => (
          <li key={i} className="flex items-start gap-2 text-[13px] text-t-secondary">
            <span aria-hidden className={t.status === "completed" ? "text-j-primary" : "text-t-muted"}>
              {GLYPH[t.status]}
            </span>
            <span className={t.status === "completed" ? "line-through text-t-muted" : ""}>
              {t.content}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/jarvis/chat-todos.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/jarvis/chat-todos.tsx frontend/src/components/jarvis/chat-todos.test.tsx
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3a — ChatTodos component
```

### Task 3: Wire the todos interception into `chat-panel.tsx`

**Files:** Modify `frontend/src/components/jarvis/chat-panel.tsx`

Context (verbatim current `tool_call` case, ~:409): it appends `{ tool, input }` to the running
agent's `toolCalls`. The `tool_result` case (~:429) attaches `result` to the LAST toolCall. Both SSE
frames carry `event.tool` (verified: `stream_adapter.py:199,228`).

- [ ] **Step 1: Add a `todos` field to the assistant message model**

Find the assistant-message type in `chat-panel.tsx` (the object updated by `updateAssistant`). Add:

```typescript
  todos?: import("@/lib/todos").Todo[];
```

(Place it beside the existing `agents` field. If the message type is imported from another module,
add the optional field there instead — the executor confirms the type's home by reading the file.)

- [ ] **Step 2: Intercept `write_todos` in the `tool_call` case**

Replace the opening of the `case "tool_call":` block (~:409) so the todos short-circuit runs first:

```typescript
      case "tool_call": {
        const todos = todosFromToolCall(event);
        if (todos) {
          updateAssistant((m) => ({ ...m, todos }));
          break;
        }
        updateAssistant((m) => ({
          ...m,
          agents: m.agents.map((a) =>
            a.agent === event.agent && a.status === "running"
              ? {
                  ...a,
                  toolCalls: [
                    ...a.toolCalls,
                    { tool: event.tool || "unknown", input: (event.input ?? {}) as Record<string, unknown> },
                  ],
                }
              : a,
          ),
        }));
        break;
      }
```

- [ ] **Step 3: Skip the `write_todos` `tool_result` chip**

At the top of the `case "tool_result":` block (~:429), add an early break so a `write_todos` result
never attaches to an unrelated chip:

```typescript
      case "tool_result":
        if (event.tool === "write_todos") break;
        // ...existing tool_result body unchanged...
```

(If the TS `ChatSSEEvent` `tool_result` variant does not declare `tool`, add `tool?: string` to that
variant's type in `@/lib/api` — the backend frame carries it.)

- [ ] **Step 4: Render `<ChatTodos>` in the assistant message**

Add the imports at the top of `chat-panel.tsx`:

```typescript
import { ChatTodos } from "./chat-todos";
import { todosFromToolCall } from "@/lib/todos";
```

In the assistant-message JSX (where `m.agents` renders), render the todos when present, e.g.:

```typescript
        {m.todos && m.todos.length > 0 && <ChatTodos todos={m.todos} />}
```

- [ ] **Step 5: Verify lint/build/tests**

Run: `cd frontend && npm run lint && npm run test && npm run build`
Expected: all green (no new backend changes; the interception is frontend-only).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/jarvis/chat-panel.tsx frontend/src/lib/api.ts
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3a — inline write_todos checklist in chat
```

**P3a checkpoint:** full frontend gate green. Backend untouched — run the backend full gate once to
confirm byte-neutrality. Then run security + quality reviews (small surface; focus: no PII/secret leak
in rendered todos, XSS via `content` — React escapes by default, confirm no `dangerouslySetInnerHTML`).

---

## Phase P3b — retire `mode` → `permission_mode` (API + frontend; LIVE)

### Task 4: Backend — remove `mode` from `ChatRequest`, forward fixed `mode="ask"`

**Files:** Modify `backend/src/api/routes_chat.py` (`ChatRequest` :44-55; handler :433). Test:
`backend/tests/test_routes_chat_permission_mode.py` (create).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_routes_chat_permission_mode.py`

```python
"""P3b: the chat HTTP contract drops the user-facing legacy ``mode``; the interactive handler
forwards a fixed ``mode="ask"`` so live default behavior is byte-identical, while ``permission_mode``
is the user-facing field."""

from __future__ import annotations

from src.api.routes_chat import ChatRequest


def test_chat_request_has_no_user_mode_field():
    # ``mode`` is removed from the request contract (retired in favor of permission_mode).
    assert "mode" not in ChatRequest.model_fields


def test_chat_request_still_has_permission_mode():
    assert "permission_mode" in ChatRequest.model_fields
    # unchanged default in P3b (P3c makes it Optional[...] = None)
    assert ChatRequest(message="hi").permission_mode == "auto"


def test_chat_request_ignores_a_client_sent_mode():
    # A client that still POSTs ``mode`` is not rejected; the field is simply dropped.
    req = ChatRequest.model_validate({"message": "hi", "mode": "plan"})
    assert not hasattr(req, "mode")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_routes_chat_permission_mode.py -q`
Expected: FAIL — `mode` still in `model_fields`.

- [ ] **Step 3: Implement** — in `backend/src/api/routes_chat.py`

Remove the `mode` field from `ChatRequest` (delete line `mode: str = "ask"  # ask, plan, execute`).
In the handler, change the forwarded `mode=req.mode` (~:433) to a fixed value:

```python
                mode="ask",  # P3b: legacy planning axis retired from the API; interactive default.
```

Leave `permission_mode=req.permission_mode` (~:436) unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_routes_chat_permission_mode.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full gate + commit**

Run: `cd backend && uv run pytest tests/ --ignore=tests/e2e -q && uv run ruff check src tests`
Expected: green (the internal `mode` param on `process_message*` is untouched; pinned callers
`schedule_dispatch`/`routes_ws` still pass their explicit `mode`).

```bash
git add backend/src/api/routes_chat.py backend/tests/test_routes_chat_permission_mode.py
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3b — drop user-facing mode from ChatRequest
```

### Task 5: Frontend — migrate `command-store` to `permissionMode`

**Files:** Modify `frontend/src/stores/command-store.ts`. Test: `frontend/src/stores/command-store.test.ts` (create).

- [ ] **Step 1: Write the failing test** — `frontend/src/stores/command-store.test.ts`

```typescript
import { test, expect, beforeEach } from "vitest";
import { useCommandStore } from "./command-store";

beforeEach(() => useCommandStore.setState({ permissionMode: "auto" }));

test("defaults permissionMode to auto", () => {
  expect(useCommandStore.getState().permissionMode).toBe("auto");
});

test("setPermissionMode updates the value", () => {
  useCommandStore.getState().setPermissionMode("bypass");
  expect(useCommandStore.getState().permissionMode).toBe("bypass");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/stores/command-store.test.ts`
Expected: FAIL — `permissionMode`/`setPermissionMode` do not exist.

- [ ] **Step 3: Implement** — in `frontend/src/stores/command-store.ts`

Replace the mode type + state. Change `export type CommandMode = "ask" | "plan" | "execute";` to:

```typescript
export type PermissionMode = "auto" | "ask" | "bypass";
```

In `CommandHistoryEntry`, change `mode: CommandMode;` → `permissionMode: PermissionMode;`. In the
`CommandState` interface, replace:

```typescript
  // Permission mode & scope
  permissionMode: PermissionMode;
  setPermissionMode: (mode: PermissionMode) => void;
```

In the store body, replace the `mode`/`setMode` initializers:

```typescript
  permissionMode: "auto",
  setPermissionMode: (permissionMode) => set({ permissionMode }),
```

And in `addToHistory`, change `mode: get().mode` → `permissionMode: get().permissionMode`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/stores/command-store.test.ts`
Expected: PASS (2 tests). (Type errors in consumers are expected until Tasks 6-7 — that's fine for
this unit; `npm run build` runs after Task 7.)

- [ ] **Step 5: Commit** (defer build to Task 7)

```bash
git add frontend/src/stores/command-store.ts frontend/src/stores/command-store.test.ts
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3b — command-store permissionMode
```

### Task 6: Frontend — `streamChat` sends `permission_mode`; `chat-panel` send site

**Files:** Modify `frontend/src/lib/api.ts` (`streamChat` :224-250); `frontend/src/components/jarvis/chat-panel.tsx` (:535). Test: `frontend/src/lib/api-stream-chat.test.ts` (create).

- [ ] **Step 1: Write the failing test** — `frontend/src/lib/api-stream-chat.test.ts`

```typescript
import { test, expect, vi, afterEach } from "vitest";
import { streamChat } from "./api";

afterEach(() => vi.unstubAllGlobals());

function okStream(): Response {
  return {
    ok: true,
    body: { getReader: () => ({ read: async () => ({ done: true, value: undefined }) }) },
  } as unknown as Response;
}

test("streamChat puts permission_mode in the POST body", async () => {
  const fetchMock = vi.fn().mockResolvedValue(okStream());
  vi.stubGlobal("fetch", fetchMock);

  await streamChat("hi", () => {}, undefined, "conv_1", "bypass");

  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/jarvis/chat");
  const body = JSON.parse((init as RequestInit).body as string);
  expect(body).toMatchObject({ message: "hi", surface: "web", conversation_id: "conv_1", permission_mode: "bypass" });
  expect(body).not.toHaveProperty("mode");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/api-stream-chat.test.ts`
Expected: FAIL — body has `mode`, not `permission_mode`.

- [ ] **Step 3: Implement** — in `frontend/src/lib/api.ts`, change `streamChat`'s last param + body:

```typescript
export async function streamChat(
  message: string,
  onEvent: (event: ChatSSEEvent) => void,
  signal?: AbortSignal,
  conversationId?: string | null,
  permissionMode?: string,
): Promise<void> {
  const body: Record<string, unknown> = { message, surface: "web" };
  if (conversationId) body.conversation_id = conversationId;
  if (permissionMode) body.permission_mode = permissionMode;
  // ...rest of the function unchanged...
```

In `frontend/src/components/jarvis/chat-panel.tsx` (~:535), change the last `streamChat` arg:

```typescript
        useCommandStore.getState().permissionMode,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/api-stream-chat.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit** (build after Task 7)

```bash
git add frontend/src/lib/api.ts frontend/src/components/jarvis/chat-panel.tsx frontend/src/lib/api-stream-chat.test.ts
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3b — streamChat sends permission_mode
```

### Task 7: Frontend — swap the three pickers to `auto`/`ask`/`bypass`

**Files:** Modify `frontend/src/components/feature/command/command-composer.tsx`,
`frontend/src/components/shell/command-launcher.tsx`, `frontend/src/app/chat/page.tsx`.

There is no clean unit-test seam for these presentational pickers; correctness is verified by the
store test (Task 5), `npm run build` (type-checks the `permissionMode` consumers), and a manual
render check. Descriptions below make the intent honest.

- [ ] **Step 1: `command-composer.tsx`** — replace the `modes` array (:11-15) and the store hook (:17):

```typescript
const modes = [
  { value: "auto", label: "Auto" },
  { value: "ask", label: "Ask" },
  { value: "bypass", label: "Bypass" },
] as const;
```

```typescript
  const { permissionMode, setPermissionMode } = useCommandStore();
```

In the picker JSX, change `onClick={() => setMode(m.value)}` → `onClick={() => setPermissionMode(m.value)}`
and `mode === m.value` → `permissionMode === m.value`.

- [ ] **Step 2: `command-launcher.tsx`** — replace `MODES` + `SUGGESTIONS` (:19-29):

```typescript
const MODES: { value: PermissionMode; label: string; icon: string }[] = [
  { value: "auto", label: "Auto", icon: "◐" },
  { value: "ask", label: "Ask", icon: "?" },
  { value: "bypass", label: "Bypass", icon: "▶" },
];

const SUGGESTIONS: { mode: PermissionMode; text: string }[] = [
  { mode: "auto", text: "Triage my inbox from this morning" },
  { mode: "ask", text: "Plan a weekly digest for my team" },
  { mode: "bypass", text: "Sync Linear issues to Notion" },
];
```

Update the import `import { ..., type CommandMode }` → `type PermissionMode`; change the store hook
(:33) to `const { permissionMode, setPermissionMode } = useCommandStore();`; in `selectSuggestion`
change `setMode(suggestion.mode)` → `setPermissionMode(suggestion.mode)`; in the mode bar change
`onClick={() => setMode(m.value)}` → `setPermissionMode`, `mode === m.value` → `permissionMode === m.value`.

- [ ] **Step 3: `app/chat/page.tsx`** — replace `MODES` (:158-162) and the store hook (:33):

```typescript
  const MODES = [
    { value: "auto" as const, label: "Auto" },
    { value: "ask" as const, label: "Ask" },
    { value: "bypass" as const, label: "Bypass" },
  ];
```

```typescript
  const { permissionMode, setPermissionMode } = useCommandStore();
```

In the picker JSX change `onClick={() => setMode(m.value)}` → `setPermissionMode`, `mode === m.value`
→ `permissionMode === m.value`.

- [ ] **Step 4: Verify the full frontend gate**

Run: `cd frontend && npm run lint && npm run test && npm run build`
Expected: all green — `build` confirms every `permissionMode` consumer type-checks and no `setMode`/
`mode` references to the old store field remain (grep `git grep -n "\.mode\b\|setMode" frontend/src`
to confirm none are the command-store's retired field).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/feature/command/command-composer.tsx frontend/src/components/shell/command-launcher.tsx frontend/src/app/chat/page.tsx
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3b — swap chat pickers to permission_mode
```

**P3b checkpoint:** backend full gate + frontend full gate green. Security + quality reviews — focus:
the pinned callers (`schedule_dispatch` `mode="execute"`, `routes_ws` `mode="ask"`) are byte-identical
(the internal `mode` param is untouched); the legacy path stays ungated; no residual references to the
retired store `mode` field.

---

## Phase P3c — per-workspace `permission_mode` default (backend-authoritative)

### Task 8: Backend — `workspace_default_permission_mode` helper

**Files:** Modify `backend/src/services/workspace_entitlements.py` (add helper). Test:
`backend/tests/test_workspace_default_permission_mode.py` (create; mirror `test_workspace_entitlements.py`).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_workspace_default_permission_mode.py`

```python
"""P3c: per-workspace default permission mode, read from Workspace.settings JSONB (no migration).
Fail-safe to ``"auto"`` on unset / bad value / missing workspace / DB error."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services.workspace_entitlements import workspace_default_permission_mode

pytestmark = pytest.mark.asyncio


class _FakeDB:
    def __init__(self, workspace):
        self._workspace = workspace

    async def get(self, _model, _key):
        return self._workspace


class _FakeFactory:
    def __init__(self, workspace):
        self._workspace = workspace

    def __call__(self):
        return self

    async def __aenter__(self):
        return _FakeDB(self._workspace)

    async def __aexit__(self, *_a):
        return False


class _RaisingFactory:
    def __call__(self):
        raise RuntimeError("db down")


async def test_returns_stored_value():
    ws = MagicMock()
    ws.settings = {"default_permission_mode": "ask"}
    assert await workspace_default_permission_mode(_FakeFactory(ws), "ws_1") == "ask"


async def test_defaults_auto_when_absent():
    ws = MagicMock()
    ws.settings = {"allow_bypass": True}
    assert await workspace_default_permission_mode(_FakeFactory(ws), "ws_1") == "auto"


async def test_defaults_auto_when_settings_none():
    ws = MagicMock()
    ws.settings = None
    assert await workspace_default_permission_mode(_FakeFactory(ws), "ws_1") == "auto"


async def test_defaults_auto_when_value_invalid():
    ws = MagicMock()
    ws.settings = {"default_permission_mode": "garbage"}
    assert await workspace_default_permission_mode(_FakeFactory(ws), "ws_1") == "auto"


async def test_defaults_auto_when_workspace_missing():
    assert await workspace_default_permission_mode(_FakeFactory(None), "ws_1") == "auto"


async def test_defaults_auto_on_error():
    assert await workspace_default_permission_mode(_RaisingFactory(), "ws_1") == "auto"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_workspace_default_permission_mode.py -q`
Expected: FAIL — `ImportError: cannot import name 'workspace_default_permission_mode'`.

- [ ] **Step 3: Implement** — append to `backend/src/services/workspace_entitlements.py`

```python
VALID_PERMISSION_MODES = ("auto", "ask", "bypass")


async def workspace_default_permission_mode(db_factory, workspace_id: str) -> str:
    """The workspace's default chat ``permission_mode`` (``auto``/``ask``/``bypass``).

    Reads ``Workspace.settings["default_permission_mode"]`` (JSONB; NO migration). Fail-safe to
    ``"auto"`` — the least-authority default — on a missing workspace, an unset/invalid value, or
    any error. This is the fallback the interactive chat handler applies when the request omits
    ``permission_mode``; it is NEVER read on the pinned-caller / autonomous paths.
    """
    try:
        async with db_factory() as db:
            workspace = await db.get(Workspace, workspace_id)
            if workspace is None:
                return "auto"
            value = (workspace.settings or {}).get("default_permission_mode")
            return value if value in VALID_PERMISSION_MODES else "auto"
    except Exception:
        logger.warning(
            "workspace_default_permission_mode lookup failed for %s — defaulting to auto (fail-safe)",
            workspace_id,
            exc_info=True,
        )
        return "auto"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_workspace_default_permission_mode.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/workspace_entitlements.py backend/tests/test_workspace_default_permission_mode.py
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3c — workspace_default_permission_mode helper
```

### Task 9: Backend — GET/PUT endpoints for the workspace default

**Files:** Create `backend/src/api/routes_workspace_settings.py`; modify `backend/src/api/app.py`
(register router). Test: `backend/tests/test_routes_workspace_settings.py` (create).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_routes_workspace_settings.py`

```python
"""P3c: per-workspace default permission mode GET/PUT. Validates the value and JSONB-merges
without clobbering sibling keys (e.g. allow_bypass)."""

from __future__ import annotations

import pytest

from src.api.routes_workspace_settings import (
    DefaultPermissionModeRequest,
    _merged_settings,
)

pytestmark = pytest.mark.asyncio


def test_request_rejects_bad_value():
    with pytest.raises(ValueError):
        DefaultPermissionModeRequest(default_permission_mode="garbage")


def test_request_accepts_valid_values():
    for v in ("auto", "ask", "bypass"):
        assert DefaultPermissionModeRequest(default_permission_mode=v).default_permission_mode == v


def test_merged_settings_preserves_siblings():
    # Writing the default must not drop allow_bypass or other keys.
    merged = _merged_settings({"allow_bypass": True, "foo": 1}, "ask")
    assert merged == {"allow_bypass": True, "foo": 1, "default_permission_mode": "ask"}


def test_merged_settings_from_none():
    assert _merged_settings(None, "bypass") == {"default_permission_mode": "bypass"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_routes_workspace_settings.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement** — `backend/src/api/routes_workspace_settings.py`

```python
"""Per-workspace chat settings — the default ``permission_mode`` (auto/ask/bypass).

Stored in ``Workspace.settings["default_permission_mode"]`` (JSONB; NO migration). Workspace-scoped
via ``get_current_workspace_id`` (mirrors routes_trust). The default is the fallback the interactive
chat handler applies when a request omits ``permission_mode``; it is never read on pinned/autonomous
paths.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_current_workspace_id, get_session
from src.models.users import User, Workspace
from src.services.workspace_entitlements import (
    VALID_PERMISSION_MODES,
    workspace_default_permission_mode,
)

router = APIRouter()


class DefaultPermissionModeResponse(BaseModel):
    default_permission_mode: str


class DefaultPermissionModeRequest(BaseModel):
    default_permission_mode: Literal["auto", "ask", "bypass"]


def _merged_settings(current: dict | None, value: str) -> dict:
    """Return a NEW settings dict with the default set, preserving all sibling keys."""
    return {**(current or {}), "default_permission_mode": value}


def _get_db_factory():
    """Async-context session factory for the fail-safe helper read."""
    from src.models.database import get_session_factory

    return get_session_factory()


@router.get("/v1/workspace/permission-mode-default", response_model=DefaultPermissionModeResponse)
async def get_default_permission_mode(
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """The workspace's default chat permission mode (fail-safe ``auto``)."""
    value = await workspace_default_permission_mode(_get_db_factory(), workspace_id)
    return DefaultPermissionModeResponse(default_permission_mode=value)


@router.put("/v1/workspace/permission-mode-default", response_model=DefaultPermissionModeResponse)
async def set_default_permission_mode(
    req: DefaultPermissionModeRequest,
    user: User = Depends(get_current_user),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Set the workspace default (JSONB merge; preserves allow_bypass + other keys)."""
    if req.default_permission_mode not in VALID_PERMISSION_MODES:
        raise HTTPException(status_code=400, detail="Invalid permission mode")
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace.settings = _merged_settings(workspace.settings, req.default_permission_mode)
    await db.commit()
    return DefaultPermissionModeResponse(default_permission_mode=req.default_permission_mode)
```

Note: reassign `workspace.settings` to a NEW dict (not in-place mutate) so SQLAlchemy detects the
JSONB change (in-place mutation of a JSONB dict is not tracked without `MutableDict`).

- [ ] **Step 4: Register the router** — in `backend/src/api/app.py`, beside the other `include_router`
calls (~:504):

```python
    from src.api.routes_workspace_settings import router as workspace_settings_router

    app.include_router(workspace_settings_router, tags=["workspace-settings"])
```

(Match the existing import style in `app.py` — if routers are imported at the top, add it there.)

- [ ] **Step 5: Run tests + full gate**

Run: `cd backend && uv run pytest tests/test_routes_workspace_settings.py -q && uv run pytest tests/ --ignore=tests/e2e -q`
Expected: PASS + full gate green.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/routes_workspace_settings.py backend/src/api/app.py backend/tests/test_routes_workspace_settings.py
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3c — workspace permission-mode-default GET/PUT
```

### Task 10: Backend — optional `permission_mode` + handler resolution

**Files:** Modify `backend/src/api/routes_chat.py` (`ChatRequest.permission_mode`; handler). Test:
extend `backend/tests/test_routes_chat_permission_mode.py`.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_routes_chat_permission_mode.py`

```python
def test_permission_mode_is_optional_default_none():
    # P3c: the field becomes optional so the handler can substitute the per-workspace default.
    assert ChatRequest(message="hi").permission_mode is None
    assert ChatRequest(message="hi", permission_mode="bypass").permission_mode == "bypass"


def test_permission_mode_still_rejects_typos():
    import pytest

    with pytest.raises(ValueError):
        ChatRequest(message="hi", permission_mode="banana")
```

Also add a resolution unit test — `backend/tests/test_chat_permission_mode_resolution.py`:

```python
"""P3c: the interactive chat handler substitutes the per-workspace default when the request omits
permission_mode; an explicit per-turn value wins. Resolution is at the handler, NOT _process_core
(pinned callers never receive a workspace-default-derived value)."""

from __future__ import annotations

import pytest

from src.api.routes_chat import _resolve_request_permission_mode

pytestmark = pytest.mark.asyncio


class _Factory:
    def __init__(self, value):
        self._value = value

    def __call__(self):
        return self

    async def __aenter__(self):
        db = type("_DB", (), {})()

        async def _get(_m, _k):
            ws = type("_WS", (), {})()
            ws.settings = {"default_permission_mode": self._value} if self._value else None
            return ws

        db.get = _get
        return db

    async def __aexit__(self, *_a):
        return False


async def test_explicit_value_wins():
    assert await _resolve_request_permission_mode("bypass", _Factory("ask"), "ws_1") == "bypass"


async def test_none_falls_back_to_workspace_default():
    assert await _resolve_request_permission_mode(None, _Factory("ask"), "ws_1") == "ask"


async def test_none_and_no_default_is_auto():
    assert await _resolve_request_permission_mode(None, _Factory(None), "ws_1") == "auto"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_routes_chat_permission_mode.py tests/test_chat_permission_mode_resolution.py -q`
Expected: FAIL — `permission_mode` still required-default `"auto"`; `_resolve_request_permission_mode` missing.

- [ ] **Step 3: Implement** — in `backend/src/api/routes_chat.py`

Change the field:

```python
    permission_mode: Literal["auto", "ask", "bypass"] | None = None
```

Add a resolution helper (module-level, near the handler):

```python
async def _resolve_request_permission_mode(requested, db_factory, workspace_id: str) -> str:
    """Resolve the effective per-turn permission_mode for an INTERACTIVE chat request.

    An explicit value wins; when omitted (``None``), substitute the per-workspace default
    (fail-safe ``auto``). Called ONLY from the interactive handler — the pinned callers
    (schedule_dispatch, routes_ws) invoke ``process_message*`` directly and never reach here,
    so a workspace ``bypass`` default can never leak onto scheduled/autonomous turns.
    """
    if requested is not None:
        return requested
    return await workspace_default_permission_mode(db_factory, workspace_id)
```

Add the import at the top of `routes_chat.py`:

```python
from src.services.workspace_entitlements import workspace_default_permission_mode
```

In the handler, resolve before the `process_message_events` call and pass the resolved value:

```python
    resolved_permission_mode = await _resolve_request_permission_mode(
        req.permission_mode, get_session_factory(), workspace_id
    )
    # ...
                permission_mode=resolved_permission_mode,
```

(`get_session_factory` is already imported in `routes_chat.py` per the user-message-persist block at
:402 — confirm and reuse; else `from src.models.database import get_session_factory`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_routes_chat_permission_mode.py tests/test_chat_permission_mode_resolution.py -q`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

Run: `cd backend && uv run pytest tests/ --ignore=tests/e2e -q && uv run ruff check src tests`
Expected: green. Confirm no pinned-caller test regressed (they pass explicit `permission_mode` /
default and bypass the handler resolver entirely).

```bash
git add backend/src/api/routes_chat.py backend/tests/test_routes_chat_permission_mode.py backend/tests/test_chat_permission_mode_resolution.py
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3c — backend-authoritative permission_mode resolution
```

### Task 11: Frontend — api.ts GET/PUT + settings dropdown

**Files:** Modify `frontend/src/lib/api.ts` (add GET/PUT); `frontend/src/components/settings/settings-modal.tsx`; `frontend/src/components/settings/policy-tab.tsx`. Test: extend `frontend/src/components/settings/settings-modal.test.tsx` or a new `policy-tab.test.tsx`.

- [ ] **Step 1: Add the API functions** — in `frontend/src/lib/api.ts` `// ── Settings ──` region:

```typescript
export function fetchWorkspaceDefaultPermissionMode(): Promise<{ default_permission_mode: string }> {
  return api("/workspace/permission-mode-default");
}

export function setWorkspaceDefaultPermissionMode(
  mode: string,
): Promise<{ default_permission_mode: string }> {
  return api("/workspace/permission-mode-default", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default_permission_mode: mode }),
  });
}
```

- [ ] **Step 2: Write the failing test** — `frontend/src/components/settings/policy-tab.test.tsx`

```typescript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { PolicyTab } from "./policy-tab";

const PERMISSION_MODES = [
  { value: "auto", label: "Auto", description: "Confirm only risky writes" },
  { value: "ask", label: "Ask", description: "Confirm every write" },
  { value: "bypass", label: "Bypass", description: "Never confirm" },
];

test("renders the default-permission-mode options and fires the change callback", async () => {
  const onChange = vi.fn();
  render(
    <PolicyTab
      policyMode="approval_required"
      policyModes={[{ value: "approval_required", label: "Approval Required", description: "" }]}
      policyLoading={false}
      onPolicyChange={() => {}}
      defaultPermissionMode="auto"
      permissionModes={PERMISSION_MODES}
      permissionLoading={false}
      onDefaultPermissionModeChange={onChange}
    />,
  );
  await userEvent.click(screen.getByText("Bypass"));
  expect(onChange).toHaveBeenCalledWith("bypass");
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/settings/policy-tab.test.tsx`
Expected: FAIL — `PolicyTab` does not accept the new props / no "Bypass" option.

- [ ] **Step 4: Implement** — extend `PolicyTab` props + render a second section in `policy-tab.tsx`:

```typescript
interface PermissionModeOption {
  value: string;
  label: string;
  description: string;
}

interface PolicyTabProps {
  policyMode: string;
  policyModes: PolicyMode[];
  policyLoading: boolean;
  onPolicyChange: (value: string) => void;
  defaultPermissionMode: string;
  permissionModes: PermissionModeOption[];
  permissionLoading: boolean;
  onDefaultPermissionModeChange: (value: string) => void;
}
```

Add, after the existing "Overall posture" block, a new section (same button-row visual language):

```typescript
      <div>
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Default chat permission mode
        </p>
        <div className="space-y-2">
          {permissionModes.map((pm) => {
            const isActive = defaultPermissionMode === pm.value;
            return (
              <button
                key={pm.value}
                type="button"
                onClick={() => onDefaultPermissionModeChange(pm.value)}
                disabled={permissionLoading}
                className={`w-full text-left rounded-[var(--radius-lg)] border p-4 transition-all duration-150 cursor-pointer ${
                  isActive ? "border-j-primary/40 bg-j-primary-soft" : "border-b-secondary bg-surface-1 hover:bg-surface-2"
                } disabled:opacity-50`}
              >
                <p className="text-[13px] font-medium text-t-primary">{pm.label}</p>
                <p className="text-xs text-t-tertiary mt-0.5">{pm.description}</p>
              </button>
            );
          })}
        </div>
      </div>
```

- [ ] **Step 5: Wire it in `settings-modal.tsx`** — add the const, state, GET-on-open, PUT handler:

```typescript
const PERMISSION_MODES = [
  { value: "auto", label: "Auto", description: "Confirm only risky writes" },
  { value: "ask", label: "Ask", description: "Confirm every write" },
  { value: "bypass", label: "Bypass", description: "Never confirm (requires workspace entitlement)" },
];
```

```typescript
  const [defaultPermissionMode, setDefaultPermissionModeState] = useState("auto");
  const [permissionLoading, setPermissionLoading] = useState(false);
```

In the load-on-open `useEffect` (:133), add:

```typescript
    fetchWorkspaceDefaultPermissionMode()
      .then((r) => setDefaultPermissionModeState(r.default_permission_mode))
      .catch(() => {});
```

Add a handler mirroring `handlePolicyChange`:

```typescript
  const handleDefaultPermissionModeChange = useCallback(
    async (mode: string) => {
      setPermissionLoading(true);
      try {
        await setWorkspaceDefaultPermissionMode(mode);
        setDefaultPermissionModeState(mode);
        addToast("Default permission mode updated", "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setPermissionLoading(false);
      }
    },
    [addToast],
  );
```

Import the two new api functions (:4-12 block) and pass the new props to `<PolicyTab>` (:324):

```typescript
                defaultPermissionMode={defaultPermissionMode}
                permissionModes={PERMISSION_MODES}
                permissionLoading={permissionLoading}
                onDefaultPermissionModeChange={handleDefaultPermissionModeChange}
```

- [ ] **Step 6: Run test + full frontend gate**

Run: `cd frontend && npx vitest run src/components/settings/policy-tab.test.tsx && npm run lint && npm run test && npm run build`
Expected: all green. (`settings-modal.test.tsx` may need the two new api functions mocked — add them
to its existing `vi.mock("@/lib/api", ...)` block if present.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/settings/policy-tab.tsx frontend/src/components/settings/settings-modal.tsx frontend/src/components/settings/policy-tab.test.tsx
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3c — settings dropdown for workspace default
```

### Task 12: Frontend — seed the chat picker from the workspace default

**Files:** Modify `frontend/src/components/jarvis/chat-panel.tsx` (or the picker mount point).

- [ ] **Step 1: Seed on mount** — where the chat UI first mounts (e.g. `chat-panel.tsx` top-level
`useEffect`), fetch the workspace default and seed the store IF the user has not overridden this
session. Use a one-shot effect (no setState-in-render; follows the repo hook rules):

```typescript
  useEffect(() => {
    let cancelled = false;
    fetchWorkspaceDefaultPermissionMode()
      .then((r) => {
        if (!cancelled) useCommandStore.getState().setPermissionMode(r.default_permission_mode as PermissionMode);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
```

Import `fetchWorkspaceDefaultPermissionMode` and `type PermissionMode`. (This seeds the picker to the
workspace default; the user can still override per-turn — the picker always sends its explicit value,
and the backend `None`-fallback (Task 10) covers non-frontend/omitting clients.)

- [ ] **Step 2: Verify the full frontend gate**

Run: `cd frontend && npm run lint && npm run test && npm run build`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/jarvis/chat-panel.tsx
git commit -F <scratch-msg-file>   # feat(chat-permission-model): P3c — seed chat picker from workspace default
```

**P3c checkpoint:** backend + frontend full gates green. Security + quality reviews — focus: the
JSONB merge preserves `allow_bypass` (Task 9 `_merged_settings`), the workspace-default resolver is
reached ONLY by the interactive handler (Task 10 — no pinned-caller leak), and the `PUT` is
workspace-scoped (`get_current_workspace_id`, cross-tenant safe).

---

## Self-Review

**Spec coverage (design §3):**
- P3a inline todos (§3 P3a) → Tasks 1-3. ✓
- P3b remove `mode` from `ChatRequest` + fixed `mode="ask"` (§3 P3b backend) → Task 4. ✓
- P3b frontend store/api/picker swap (§3 P3b frontend) → Tasks 5-7. ✓
- P3c helper + storage (§3 P3c, D5) → Task 8. ✓
- P3c GET/PUT + JSONB-merge invariant (§3 P3c, §5) → Task 9. ✓
- P3c backend-authoritative resolution at the handler, not `_process_core` (D4, §5) → Task 10. ✓
- P3c settings dropdown + picker seed (§3 P3c frontend, D6) → Tasks 11-12. ✓
- Invariants (§5): legacy stays ungated (no gate added anywhere); pinned callers untouched (Task 4
  keeps the internal `mode` param; Task 10 resolver is handler-only); JSONB merge not clobber (Task 9).

**Placeholder scan:** none — every code step shows real code; `<scratch-msg-file>` denotes the zsh
commit-message file per the header convention.

**Type consistency:** `PermissionMode = "auto"|"ask"|"bypass"` (Task 5) is used consistently in
Tasks 6-7, 12; `Todo`/`TodoStatus` (Task 1) used in Tasks 2-3; `workspace_default_permission_mode`
(Task 8) called in Tasks 9-10; `VALID_PERMISSION_MODES` (Task 8) reused in Task 9.

**Build-order dependency:** P3b Tasks 5-7 leave consumers mid-migration between commits — `npm run
build` is deferred to Task 7 (which type-checks the whole swap). P3c Task 10 depends on Task 8's
helper. Follow task order strictly.

**Re-verify at build (from design §7):** the exact `write_todos` `input.todos` item shape; the
`ChatSSEEvent` `tool_result` variant's `tool` field (add `tool?` if absent); `get_session_factory`
import already present in `routes_chat.py`; `settings-modal.test.tsx`'s existing `@/lib/api` mock.
