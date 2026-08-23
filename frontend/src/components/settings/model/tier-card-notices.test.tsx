/**
 * §9.6 and §4.4 — what the card says when the bound provider has no credential.
 *
 * The point of the whole redesign, and the one place where getting the COPY
 * wrong is a defect rather than a nit: a card that misstates the consequence
 * sends the founder to fix something that is not broken, or leaves them
 * believing something is broken that is not.
 */

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { btn } from "../controls";
import { TierCard } from "./tier-card";
import {
  AGENTS,
  ANTHROPIC,
  FAST_BINDING,
  GROQ,
  GROQ_MODEL,
  binding,
  card,
  consequenceText,
  model,
  renderCard,
  warningFor,
} from "./tier-card-fixtures";

// ── The unconfigured provider is a consequence, not a status ──────────────

test("a warned tier renders the amber card border, the consequence and a Connect action", async () => {
  const { onConnectProvider } = renderCard({
    binding: FAST_BINDING,
    warning: warningFor(),
  });

  expect(card().className).toContain("border-j-warning/35");
  expect(card().className).not.toContain("border-b-secondary");
  expect(screen.getByText(/Groq is not connected\./)).toBeInTheDocument();

  // The slug is what the Providers tab needs; the display name is what the
  // founder reads. The button must carry one and emit the other.
  await userEvent.click(screen.getByRole("button", { name: "Connect Groq" }));
  expect(onConnectProvider).toHaveBeenCalledTimes(1);
  expect(onConnectProvider).toHaveBeenCalledWith("groq");
});

// §2.5: there is no tier fallback. Copy implying one is the defect this state
// exists to fix — the founder would wait for a recovery never coming.
test("the warning copy never promises a fallback", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor() });
  expect(consequenceText()).not.toMatch(/falls? back to/i);
  expect(consequenceText()).toMatch(/will fail/i);
});

// The server's sentence is preferred, but a warning arriving without one must
// not render an empty amber row.
test("a warning with no message still states the consequence", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor({ message: "" }) });
  expect(consequenceText()).toMatch(/no tier fallback/i);
  expect(consequenceText()).not.toMatch(/falls? back to/i);
});

// A rejection is a 422 — the save was REFUSED, so the previously saved binding
// is still running. Telling the founder their agents are failing is the same
// class of error as promising a fallback, pointed the other way.
test("a rejection with no message never claims the agents are failing", () => {
  renderCard({ binding: FAST_BINDING, rejection: warningFor({ message: "" }) });
  expect(consequenceText()).not.toMatch(/will fail/i);
  expect(consequenceText()).not.toMatch(/every agent/i);
  expect(consequenceText()).not.toMatch(/falls? back to/i);
  // It says what actually happened, and what to do about it.
  expect(consequenceText()).toMatch(/was not saved/i);
  expect(consequenceText()).toMatch(/no tier fallback/i);
});

// The two fallbacks must be genuinely different sentences — the states of the
// world they describe are opposite.
test("the warning and rejection fallbacks are different sentences", () => {
  const { unmount } = renderCard({
    binding: FAST_BINDING,
    warning: warningFor({ message: "" }),
  });
  const warned = consequenceText();
  unmount();

  renderCard({ binding: FAST_BINDING, rejection: warningFor({ message: "" }) });
  const refused = consequenceText();

  expect(warned).not.toEqual(refused);
  expect(warned).toMatch(/will fail/i);
  expect(refused).not.toMatch(/will fail/i);
});

test("a warned card names the provider by its display name, not its slug", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor({ message: "" }) });
  expect(screen.getByRole("button", { name: "Connect Groq" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Connect groq" })).toBeNull();
});

// ── §4.4: a 422 lands on the card that caused it ──────────────────────────

test("a rejection renders on the card and outranks a warning", () => {
  const rejection = warningFor({
    message: "Groq resolves no credential. This binding was not saved.",
  });
  renderCard({ binding: FAST_BINDING, warning: warningFor(), rejection });

  expect(screen.getByText(rejection.message)).toBeInTheDocument();
  // The older warning's sentence is gone — one slot, and the newer fact wins.
  expect(screen.queryByText(/every agent on Fast will fail/)).toBeNull();
  expect(card().className).toContain("border-j-warning/35");
});

test("a rejection alone renders without any warning present", () => {
  renderCard({
    binding: FAST_BINDING,
    rejection: warningFor({ message: "Groq resolves no credential." }),
  });
  expect(screen.getByText("Groq resolves no credential.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Connect Groq" })).toBeInTheDocument();
});

// ── A warning is about the provider it names, not about the card ──────────
// The founder does what the card asked by the OTHER route: they rebind the
// tier instead of connecting the provider. The server's `warnings` array is
// unchanged until the next save, so nothing but this check stops the card
// showing "Claude Opus 4.5 · Anthropic" in amber above a Connect Groq button.

test("a warning goes quiet once the draft names a different provider", () => {
  renderCard({ binding: { scope_key: "fast" }, warning: warningFor() });

  expect(card().className).toContain("border-b-secondary");
  expect(card().className).not.toContain("border-j-warning/35");
  expect(screen.queryByRole("button", { name: /^Connect/ })).toBeNull();
  expect(screen.queryByText(/is not connected/)).toBeNull();
  // The grid is back to its idle colours…
  expect(screen.getByLabelText(/^Model/).className).not.toContain(
    "border-j-warning/45",
  );
  // …and the meta row it was hiding comes back.
  expect(screen.getByText("$5 / $25 per Mtok")).toBeInTheDocument();
});

// A rejection needs no such check — it describes the binding just attempted,
// whose provider IS the draft's.
test("a rejection is shown even though it is checked against nothing", () => {
  renderCard({
    binding: FAST_BINDING,
    rejection: warningFor({ message: "Refused." }),
  });
  expect(screen.getByText("Refused.")).toBeInTheDocument();
});

// ── The rejection's announcement must actually be announced ───────────────
// Flipping `role="alert"` onto a node already in the DOM is the documented
// unreliable case, and warning→rejection is the likeliest real sequence there
// is: a revoked provider, a save, a 422.

test("the consequence node remounts when a warning becomes a rejection", () => {
  const { rerender } = renderCard({
    binding: FAST_BINDING,
    warning: warningFor(),
  });
  const warned = screen.getByText(/Groq is not connected/);
  expect(warned).not.toHaveAttribute("role");

  rerender(
    <TierCard
      binding={binding(FAST_BINDING)}
      models={[model(), GROQ_MODEL]}
      providers={[ANTHROPIC, GROQ]}
      agents={AGENTS}
      description="Cheap and quick."
      warning={warningFor()}
      rejection={warningFor({ message: "Refused." })}
      onChange={vi.fn()}
      onOpenPicker={vi.fn()}
      onConnectProvider={vi.fn()}
    />,
  );

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("Refused.");
  // A live region is registered at INSERTION. Same node = no announcement.
  expect(alert).not.toBe(warned);
});

// Belt and braces: a live region can miss its moment, a description cannot.
test("the card is described by its consequence, and only when there is one", () => {
  const { unmount } = renderCard({
    binding: FAST_BINDING,
    warning: warningFor(),
  });
  const describedBy = screen
    .getByRole("region", { name: "Fast" })
    .getAttribute("aria-describedby");
  expect(describedBy).toBeTruthy();
  expect(document.getElementById(describedBy as string)?.textContent).toMatch(
    /Groq is not connected/,
  );
  unmount();

  renderCard();
  expect(screen.getByRole("region", { name: "Reasoning" })).not.toHaveAttribute(
    "aria-describedby",
  );
});

// ── §9.6: substitution, not addition ──────────────────────────────────────

test("the warning replaces the meta row rather than adding a second one", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor() });
  expect(screen.queryByText(/per Mtok/)).toBeNull();
  expect(screen.queryByText("Changed — not saved")).toBeNull();
});

// The amber Model-control border lives INSIDE the grid, so it can only be
// asked for from the card. This is the one §9.6 substitution the card cannot
// make itself.
test("the warning is forwarded to the binding grid", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor() });
  expect(screen.getByLabelText(/^Model/).className).toContain(
    "border-j-warning/45",
  );
  expect(screen.getByText("Groq").className).toContain("text-j-warning");
});

test("a rejection recolours the Model control for the same reason a warning does", () => {
  renderCard({
    binding: FAST_BINDING,
    rejection: warningFor({ message: "Refused." }),
  });
  expect(screen.getByLabelText(/^Model/).className).toContain(
    "border-j-warning/45",
  );
});

test("a healthy card carries neither the amber border nor a Connect action", () => {
  renderCard();
  expect(card().className).toContain("border-b-secondary");
  expect(card().className).not.toContain("border-j-warning/35");
  expect(screen.queryByRole("button", { name: /^Connect/ })).toBeNull();
  expect(screen.getByLabelText(/^Model/).className).not.toContain(
    "border-j-warning/45",
  );
});

// ── §9.3: the Connect action comes from the shared button primitive ───────
// Not a fourth hand-rolled copy of the same geometry. §9.3 puts ghost at 400
// and only primary at 500, which every hand-rolled copy got wrong.

test("the Connect action is the shared md warning ghost", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor() });
  const connect = screen.getByRole("button", { name: "Connect Groq" });
  expect(connect.className).toEqual(btn({ size: "md", variant: "warning" }));
  expect(connect.className).toContain("font-normal");
  expect(connect.className).not.toContain("font-medium");
  expect(connect.className).toContain("border-j-warning/40");
  expect(connect.className).toContain("text-j-warning");
  // §9.3's md row, including its mobile height.
  expect(connect.className).toContain("h-[44px]");
  expect(connect.className).toContain("sm:h-[32px]");
});

test("disabled turns off the grid and the Connect action alike", () => {
  renderCard({ binding: FAST_BINDING, warning: warningFor(), disabled: true });
  expect(screen.getByLabelText(/^Model/)).toBeDisabled();
  expect(screen.getByRole("button", { name: "Connect Groq" })).toBeDisabled();
});
