import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { UnitCard } from "./unit-card";
import type { Unit } from "@/lib/types/unit";

function unit(partial: Partial<Unit> = {}): Unit {
  return {
    body: "Sarah is asking for a decision by Friday.\n\nMore detail here.",
    quotes: [
      {
        text: "Can you get back to me by Friday?",
        who: "Sarah Chen",
        when: "2026-08-21T14:14:00Z",
      },
    ],
    ...partial,
    frame: {
      key: "gmail:email_thread:t_1",
      group_key: null,
      kind: "proposal",
      status: "needs_you",
      headline: "Sarah Chen - Series A term sheet",
      source: "gmail",
      entity_type: "email_thread",
      occurred_at: "2026-08-21T14:14:00Z",
      updated_at: "2026-08-21T14:14:00Z",
      importance: 0.8,
      event_count: 3,
      affordances: [
        { capability: "email.send", label: "Draft a reply", variant: "primary" },
        {
          capability: "system.schedule_reminder",
          label: "Remind me Thursday",
          variant: "secondary",
        },
      ],
      ...(partial.frame ?? {}),
    },
  };
}

function contextText(): string {
  return screen.getByTestId("unit-context").textContent ?? "";
}

test("renders the headline", () => {
  render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  expect(screen.getByText("Sarah Chen - Series A term sheet")).toBeInTheDocument();
});

test("renders the headline as plain text even if it contains markdown", () => {
  const u = unit();
  u.frame.headline = "**URGENT** [Verify](https://phish.example)";
  const { container } = render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(container.querySelector("a")).toBeNull();
  expect(container.querySelector("strong")).toBeNull();
  expect(container.textContent).toContain("**URGENT**");
});

test("renders only the first paragraph of the body", () => {
  render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  expect(screen.getByText(/Sarah is asking for a decision by Friday/)).toBeInTheDocument();
  expect(screen.queryByText(/More detail here/)).toBeNull();
});

test("splits paragraphs on a CRLF blank line, like the backend does", () => {
  const u = unit({ body: "Para one is the claim.\r\n\r\nPara two is detail." });
  render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(screen.getByText("Para one is the claim.")).toBeInTheDocument();
  expect(screen.queryByText(/Para two is detail/)).toBeNull();
});

test("splits paragraphs on a blank line that carries whitespace", () => {
  const u = unit({ body: "Para one is the claim.\n \t \nPara two is detail." });
  render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(screen.getByText("Para one is the claim.")).toBeInTheDocument();
  expect(screen.queryByText(/Para two is detail/)).toBeNull();
});

test("skips a leading heading — a label is not the claim", () => {
  const u = unit({ body: "# Heading label\n\nThe claim itself lives here." });
  render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(screen.getByText("The claim itself lives here.")).toBeInTheDocument();
  expect(screen.queryByText(/Heading label/)).toBeNull();
});

test("joins soft-wrapped lines of the first paragraph with a space", () => {
  const u = unit({ body: "The claim starts here\nand wraps to a second line.\n\nDetail." });
  render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(
    screen.getByText("The claim starts here and wraps to a second line."),
  ).toBeInTheDocument();
});

test("renders the context line with the event count", () => {
  render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  expect(contextText()).toContain("gmail");
  expect(contextText()).toContain("3 messages");
});

test("renders the entity type in the context line", () => {
  render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  const text = contextText();
  expect(text).toContain("email_thread");
  expect((text.match(/·/g) ?? []).length).toBe(2);
});

test("omits the entity-type segment and its separator when entity_type is empty", () => {
  const u = unit();
  u.frame.entity_type = "";
  render(<UnitCard unit={u} onOpen={vi.fn()} />);
  const text = contextText();
  expect(text).toContain("gmail");
  expect(text).toContain("3 messages");
  expect((text.match(/·/g) ?? []).length).toBe(1);
});

test("renders one message for a single-event unit", () => {
  const u = unit();
  u.frame.event_count = 1;
  render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(contextText()).toContain("1 message");
  expect(contextText()).not.toContain("1 messages");
});

test("renders the status pill with a Title-case frame-status label", () => {
  render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  expect(screen.getByText("Needs you")).toBeInTheDocument();
  expect(screen.queryByText("needs_you")).toBeNull();
});

test("renders the kind pill with a Title-case frame-kind label", () => {
  render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  expect(screen.getByText("Proposal")).toBeInTheDocument();
});

test("renders the quote with its attribution", () => {
  render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  const quote = screen.getByTestId("unit-quote");
  expect(within(quote).getByText(/Can you get back to me by Friday/)).toBeInTheDocument();
  expect(within(quote).getByText(/Sarah Chen/)).toBeInTheDocument();
});

test("renders quote text as plain text, never as markdown", () => {
  const u = unit();
  u.quotes = [{ text: "**URGENT** click", who: "Nobody", when: "2026-08-21T14:14:00Z" }];
  const { container } = render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(container.querySelector("strong")).toBeNull();
  expect(container.textContent).toContain("**URGENT** click");
});

test("omits the quote slot when there are no quotes", () => {
  const u = unit();
  u.quotes = [];
  render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(screen.queryByTestId("unit-quote")).toBeNull();
});

test("renders each affordance as a button", () => {
  render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Draft a reply" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remind me Thursday" })).toBeInTheDocument();
});

test("caps affordances at three", () => {
  const u = unit();
  u.frame.affordances = ["a", "b", "c", "d", "e"].map((label) => ({
    capability: "email.send",
    label,
    variant: "secondary" as const,
  }));
  render(<UnitCard unit={u} onOpen={vi.fn()} />);
  expect(screen.getByRole("button", { name: "c" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "d" })).toBeNull();
});

test("calls onOpen when the card is clicked", async () => {
  const onOpen = vi.fn();
  render(<UnitCard unit={unit()} onOpen={onOpen} />);
  await userEvent.click(screen.getByText("Sarah Chen - Series A term sheet"));
  expect(onOpen).toHaveBeenCalledOnce();
});

test("an affordance click does not also open the card", async () => {
  const onOpen = vi.fn();
  const onAct = vi.fn();
  render(<UnitCard unit={unit()} onOpen={onOpen} onAct={onAct} />);
  await userEvent.click(screen.getByRole("button", { name: "Draft a reply" }));
  expect(onAct).toHaveBeenCalledWith("email.send");
  expect(onOpen).not.toHaveBeenCalled();
});

test("a dismiss click does not also open the card", async () => {
  const onOpen = vi.fn();
  const onDismiss = vi.fn();
  render(<UnitCard unit={unit()} onOpen={onOpen} onDismiss={onDismiss} />);
  await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(onDismiss).toHaveBeenCalledOnce();
  expect(onOpen).not.toHaveBeenCalled();
});

test("renders no ellipsis anywhere", () => {
  const { container } = render(<UnitCard unit={unit()} onOpen={vi.fn()} />);
  expect(container.textContent).not.toContain("…");
  expect(container.textContent).not.toContain("...");
});
