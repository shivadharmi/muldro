/**
 * The Full's shell. Layers 2 and 4 are built; layers 1-by-archetype and 3 are
 * spec step 5.
 *
 * The one thing it must never do is render empty. "Card shows info, modal
 * shows nothing" is spec §1 defect 6, and it is what a chevron opening onto a
 * deleted SurfaceDetailModal would recreate.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UnitDetail } from "./unit-detail";
import type { Unit } from "@/lib/types/unit";

const NOW = "2026-08-22T12:00:00Z";

function unit(overrides: Partial<Unit> = {}): Unit {
  return {
    frame: {
      key: "gmail:email_thread:t1",
      group_key: null,
      kind: "proposal",
      status: "needs_you",
      headline: "Sarah Chen - Series A term sheet",
      source: "gmail",
      entity_type: "email_thread",
      occurred_at: NOW,
      updated_at: NOW,
      importance: 0,
      event_count: 3,
      affordances: [],
    },
    body: "",
    quotes: [],
    ...overrides,
  };
}

describe("UnitDetail", () => {
  it("renders the headline as plain text", () => {
    render(<UnitDetail unit={unit()} open onClose={() => {}} />);
    expect(screen.getByText("Sarah Chen - Series A term sheet")).toBeInTheDocument();
  });

  it("never passes the headline to a markdown renderer", () => {
    const { container } = render(
      <UnitDetail
        unit={unit({ frame: { ...unit().frame, headline: "**not bold** here" } })}
        open
        onClose={() => {}}
      />
    );
    expect(container.querySelector("strong")).toBeNull();
    expect(screen.getByText("**not bold** here")).toBeInTheDocument();
  });

  it("renders every quote, attributed", () => {
    render(
      <UnitDetail
        unit={unit({
          quotes: [
            { text: "Can you get back to me by Friday?", who: "Sarah Chen", when: NOW },
            { text: "Adding Priya.", who: "Tom Ford", when: NOW },
          ],
        })}
        open
        onClose={() => {}}
      />
    );
    expect(screen.getByText("Can you get back to me by Friday?")).toBeInTheDocument();
    expect(screen.getByText(/Tom Ford/)).toBeInTheDocument();
  });

  it("renders a quote as plain text, never as markdown", () => {
    // External text is carried verbatim; safety is the renderer never treating
    // it as markup. A subject that renders as a live link in muldro's voice is
    // the phishing surface spec §1 opens with.
    const { container } = render(
      <UnitDetail
        unit={unit({ quotes: [{ text: "[click](https://phish.example)", who: "X", when: NOW }] })}
        open
        onClose={() => {}}
      />
    );
    expect(container.querySelector("a")).toBeNull();
  });

  it("renders the WHOLE body, not just the lede", () => {
    render(
      <UnitDetail
        unit={unit({ body: "The lede claim.\n\nA second paragraph with detail." })}
        open
        onClose={() => {}}
      />
    );
    expect(screen.getByText(/The lede claim/)).toBeInTheDocument();
    expect(screen.getByText(/A second paragraph with detail/)).toBeInTheDocument();
  });

  it("renders affordances as buttons and calls onAct", async () => {
    const onAct = vi.fn();
    render(
      <UnitDetail
        unit={unit({
          frame: {
            ...unit().frame,
            affordances: [
              { capability: "email.send", label: "Draft a reply", variant: "primary" },
            ],
          },
        })}
        open
        onClose={() => {}}
        onAct={onAct}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Draft a reply" }));
    expect(onAct).toHaveBeenCalledWith("email.send");
  });

  it("is never empty: with no body, no quotes and no affordances it still says what it is", () => {
    render(<UnitDetail unit={unit()} open onClose={() => {}} />);
    expect(screen.getByTestId("unit-detail-context")).toHaveTextContent("gmail");
    expect(screen.getByTestId("unit-detail-context")).toHaveTextContent("3 messages");
    expect(screen.queryByText(/No detail tabs available/)).toBeNull();
  });

  it("states plainly that the reasoning has not been written yet", () => {
    // Honest absence beats a blank pane. Removed when spec step 2b lands.
    render(<UnitDetail unit={unit()} open onClose={() => {}} />);
    expect(screen.getByTestId("unit-detail-no-body")).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    const { container } = render(<UnitDetail unit={unit()} open={false} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the unit is null", () => {
    const { container } = render(<UnitDetail unit={null} open onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("closes on the close button", async () => {
    const onClose = vi.fn();
    render(<UnitDetail unit={unit()} open onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
