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
  render(
    <ChatTodos
      todos={[
        { content: "a", status: "completed" },
        { content: "b", status: "pending" },
      ]}
    />,
  );
  expect(screen.getByText(/1\s*\/\s*2/)).toBeTruthy();
});

test("renders nothing for an empty list", () => {
  const { container } = render(<ChatTodos todos={[]} />);
  expect(container.firstChild).toBeNull();
});
