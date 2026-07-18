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
 * about item shape — deepagents may vary field names across versions. */
export function todosFromToolCall(event: ChatSSEEvent): Todo[] | null {
  const e = event as { event?: string; tool?: string; input?: { todos?: unknown } };
  if (e.event !== "tool_call" || e.tool !== "write_todos") return null;
  const raw = e.input?.todos;
  if (!Array.isArray(raw)) return null;
  return raw
    .filter((t): t is Record<string, unknown> => typeof t === "object" && t !== null)
    .map((t) => ({ content: String(t.content ?? t.task ?? ""), status: normalizeStatus(t.status) }));
}
