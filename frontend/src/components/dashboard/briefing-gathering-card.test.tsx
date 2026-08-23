import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { BriefingGatheringCard } from "./briefing-gathering-card";

/**
 * This card is only reachable with at least one source connected — with none,
 * `resolveFirstRunState` returns `onboarding`. Its old copy told the founder to
 * connect a source, above a primary "Connect Sources" button, at the one moment
 * that could never be true.
 */
test("never tells a founder who has connected sources to connect a source", () => {
  render(<BriefingGatheringCard sourceCount={3} />);
  const card = screen.getByText(/being assembled/i).closest("div");
  expect(card?.textContent).not.toMatch(/connect a source/i);
  expect(screen.queryByRole("link", { name: /^connect sources$/i })).toBeNull();
});

test("says what it is watching", () => {
  render(<BriefingGatheringCard sourceCount={3} />);
  expect(screen.getByText(/watching 3 sources/i)).toBeInTheDocument();
});

test("counts one source in words, not as a bare numeral", () => {
  render(<BriefingGatheringCard sourceCount={1} />);
  expect(screen.getByText(/watching one source/i)).toBeInTheDocument();
});

test("says the briefing arrives on a schedule, so the wait is explained", () => {
  render(<BriefingGatheringCard sourceCount={2} />);
  expect(screen.getByText(/daily schedule/i)).toBeInTheDocument();
});

test("still offers a way to add more sources, as a secondary action", () => {
  render(<BriefingGatheringCard sourceCount={2} />);
  const add = screen.getByRole("link", { name: /add another source/i });
  expect(add).toHaveAttribute("href", "/integrations");
});
