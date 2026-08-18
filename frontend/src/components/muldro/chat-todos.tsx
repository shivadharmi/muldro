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
 * rewritten in place on each `write_todos` call. Content is rendered as a plain text child so
 * React escapes it (no raw HTML). */
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
