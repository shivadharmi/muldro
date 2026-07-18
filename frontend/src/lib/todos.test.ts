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

test("returns null when a write_todos event's input.todos is not an array", () => {
  const event = {
    event: "tool_call",
    tool: "write_todos",
    input: { todos: { not: "an array" } },
  } as unknown as ChatSSEEvent;
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
