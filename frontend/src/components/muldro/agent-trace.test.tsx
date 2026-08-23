import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect } from "vitest";
import { AgentTrace, type AgentStep } from "./agent-trace";

function agent(partial: Partial<AgentStep> = {}): AgentStep {
  return {
    agent: "planner",
    status: "done",
    thinking: [],
    realThinking: [],
    streamingText: "",
    toolCalls: [],
    ...partial,
  };
}

/**
 * `agent-trace.tsx`'s `AgentCard` renders `<StatusBadge status={stepStatus} />`
 * with no `dotClass` — it relies entirely on `StatusBadge`'s default
 * `statusColor(status)` lookup. This pins that the default dot colour still
 * applies for that (real) consumer now that `StatusBadge` has an optional
 * `dotClass` prop, since agent-trace has no other test coverage.
 */
test("the pipeline card's status dot falls back to statusColor when dotClass is not passed", async () => {
  const { container } = render(
    <AgentTrace agents={[agent({ status: "done" })]} plan={null} streaming={false} />,
  );

  // Theater is hidden by default — reveal the agent card.
  await userEvent.click(screen.getByText("1 step"));

  // agentStatusToStep maps "done" -> "completed" -> statusColor("completed") -> "bg-j-success".
  const dot = container.querySelector('span[aria-hidden="true"].bg-j-success');
  expect(dot).not.toBeNull();
});
