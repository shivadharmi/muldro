import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { UnitCard, ledeOf } from "./unit-card";
import type { Unit } from "@/lib/types/unit";

// Shared corpus: backend/src/view/body.py::lede_of and this file's ledeOf
// must agree on "what is paragraph 1". Read directly off disk with node:fs
// (not a static JSON import, and not `new URL(..., import.meta.url)` — Vite
// statically rewrites that pattern for asset bundling and mishandles a
// target outside the frontend package root) so this works regardless of any
// bundler restriction on reaching outside the frontend package boundary. See
// unit-card.tsx::ledeOf for why the pair must change together.
const LEDE_CORPUS_PATH = join(
  import.meta.dirname,
  "../../../../backend/tests/view/fixtures/lede_corpus.json",
);

interface LedeCase {
  name: string;
  body: string;
  lede: string;
}

const ledeCorpus: { cases: LedeCase[] } = JSON.parse(readFileSync(LEDE_CORPUS_PATH, "utf-8"));

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

test.each(ledeCorpus.cases)("ledeOf: $name", ({ body, lede }) => {
  expect(ledeOf(body)).toBe(lede);
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
