import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import {
  CHIP_VARIANTS,
  ProviderRow,
  ProviderRowSeparator,
  STATUS_PRESENTATION,
} from "./provider-row";
import type { CatalogProvider, ProviderStatus } from "@/lib/types";

function providerStatus(over: Partial<ProviderStatus> = {}): ProviderStatus {
  return {
    provider: "anthropic",
    configured: true,
    status: "valid",
    source: "workspace",
    base_url: null,
    extra_config_public: {},
    extra_config_secret_keys: [],
    catalogued: true,
    ...over,
  };
}

const ANTHROPIC: CatalogProvider = {
  provider: "anthropic",
  display_name: "Anthropic",
  auth_kind: "api_key",
  credential_fields: [],
  model_count: 4,
  docs_url: null,
};

function renderRow(
  over: Partial<ProviderStatus> = {},
  props: Partial<Parameters<typeof ProviderRow>[0]> = {},
) {
  const onToggle = vi.fn();
  const onTest = vi.fn();
  const onRemove = vi.fn();
  const rendered = render(
    <ProviderRow
      status={providerStatus(over)}
      catalog={ANTHROPIC}
      expanded={false}
      onToggle={onToggle}
      onTest={onTest}
      onRemove={onRemove}
      {...props}
    />,
  );
  return { ...rendered, onToggle, onTest, onRemove };
}

test("a workspace-owned credential offers Test, Edit and Remove", () => {
  renderRow({ source: "workspace" });
  expect(screen.getByRole("button", { name: "Test Anthropic" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Edit Anthropic" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove Anthropic" })).toBeTruthy();
});

// DELETE removes the workspace credential row and nothing else, so an inherited
// credential must not offer a button that appears to work and changes nothing.
test("Remove is absent for an env-provided credential", () => {
  renderRow({ source: "env" });
  expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
});

test("Remove is absent for a deployment-default credential", () => {
  renderRow({ source: "default" });
  expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
});

test("an env-sourced provider offers Override, not Edit", () => {
  renderRow({ source: "env" });
  expect(screen.getByRole("button", { name: "Override Anthropic" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^Edit/ })).toBeNull();
});

test("a not-connected provider offers only Connect", () => {
  renderRow({ configured: false, status: "unconfigured", source: "none" });
  expect(screen.getByRole("button", { name: "Connect Anthropic" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^Test/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
  expect(screen.getByText("Not connected")).toBeTruthy();
});

// No catalog entry means no display name and no credential schema, so nothing
// but revoking the surviving row is meaningful.
test("an uncatalogued provider renders the raw slug and offers only Remove", () => {
  renderRow(
    { provider: "legacy_vendor", catalogued: false, source: "workspace" },
    { catalog: null },
  );
  expect(screen.getByText("legacy_vendor")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove legacy_vendor" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^Test/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Edit/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Connect/ })).toBeNull();
});

// A stray whose key material no longer decrypts reports configured=false,
// source="none", catalogued=false. The row exists precisely so it can be
// removed — the ownership gate must not strand it.
test("an uncatalogued stray with no source still offers Remove", () => {
  renderRow(
    {
      provider: "legacy_vendor",
      catalogued: false,
      configured: false,
      status: "unconfigured",
      source: "none",
    },
    { catalog: null },
  );
  expect(screen.getByRole("button", { name: "Remove legacy_vendor" })).toBeTruthy();
  expect(screen.getAllByRole("button")).toHaveLength(1);
});

test("expanded renders the primary action as Cancel and toggles on click", async () => {
  const onToggle = vi.fn();
  render(
    <ProviderRow
      status={providerStatus()}
      catalog={ANTHROPIC}
      expanded
      onToggle={onToggle}
      onTest={vi.fn()}
      onRemove={vi.fn()}
    >
      <p>credential form</p>
    </ProviderRow>,
  );
  expect(screen.queryByRole("button", { name: /^Edit/ })).toBeNull();
  expect(screen.getByText("credential form")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Cancel Anthropic" }));
  expect(onToggle).toHaveBeenCalledTimes(1);
});

test("children are withheld until the row is expanded", () => {
  render(
    <ProviderRow
      status={providerStatus()}
      catalog={ANTHROPIC}
      expanded={false}
      onToggle={vi.fn()}
      onTest={vi.fn()}
      onRemove={vi.fn()}
    >
      <p>credential form</p>
    </ProviderRow>,
  );
  expect(screen.queryByText("credential form")).toBeNull();
});

// A zero-field form's required-field check is vacuously satisfied, so its Save
// button renders enabled and 400s on the backend's unknown-provider guard. The
// row refuses the body itself rather than trusting the parent never to expand.
test("an expanded uncatalogued row renders no body, even when given children", () => {
  render(
    <ProviderRow
      status={providerStatus({ provider: "legacy_vendor", catalogued: false })}
      catalog={null}
      expanded
      onToggle={vi.fn()}
      onTest={vi.fn()}
      onRemove={vi.fn()}
    >
      <p>credential form</p>
    </ProviderRow>,
  );
  expect(screen.queryByText("credential form")).toBeNull();
});

test("the toggle points at the body it controls", () => {
  render(
    <ProviderRow
      status={providerStatus()}
      catalog={ANTHROPIC}
      expanded
      onToggle={vi.fn()}
      onTest={vi.fn()}
      onRemove={vi.fn()}
    >
      <p>credential form</p>
    </ProviderRow>,
  );
  const toggle = screen.getByRole("button", { name: "Cancel Anthropic" });
  const bodyId = toggle.getAttribute("aria-controls");
  expect(bodyId).toBe("provider-row-body-anthropic");
  expect(document.getElementById(bodyId as string)?.textContent).toBe("credential form");
});

// Nothing is being controlled while the row is collapsed, so the reference must
// not dangle.
test("a collapsed row's toggle controls nothing", () => {
  renderRow();
  const toggle = screen.getByRole("button", { name: "Edit Anthropic" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(toggle).not.toHaveAttribute("aria-controls");
});

test("an expanded row's reason renders as a chip", () => {
  renderRow({}, { reason: "Needed by the Fast tier", expanded: true });
  expect(screen.getByText("Needed by the Fast tier")).toBeTruthy();
});

// A reason answers "why is this open". On a collapsed row it captions nothing,
// and a stale one would follow whichever row the founder opened next — so the
// row refuses it rather than trusting every caller to withhold it.
test("a collapsed row shows no reason, even when handed one", () => {
  renderRow({}, { reason: "Needed by the Fast tier", expanded: false });
  expect(screen.queryByText("Needed by the Fast tier")).toBeNull();
});

test("the auth kind renders as a readable label, not the raw slug", () => {
  renderRow(
    { provider: "ollama", source: "workspace", base_url: "http://localhost:11434" },
    {
      catalog: {
        ...ANTHROPIC,
        provider: "ollama",
        display_name: "Ollama",
        auth_kind: "keyless_base_url",
      },
    },
  );
  expect(screen.getByText("Base URL")).toBeTruthy();
  expect(screen.queryByText("keyless_base_url")).toBeNull();
  expect(screen.getByText("http://localhost:11434")).toBeTruthy();
});

// A rejected credential fails every tier bound to it. It must not wear the same
// amber as the merely-untested default.
test("a rejected credential reads as an error, not a warning", () => {
  renderRow({ status: "invalid" });
  expect(screen.getByText("Invalid credential").className).toContain("bg-j-error-soft");
});

test("an untested credential stays a warning", () => {
  renderRow({ status: "untested" });
  expect(screen.getByText("Untested").className).toContain("bg-j-warning-soft");
});

/** The dot, located by the fact that it is the row's only decorative element.
 *  That is asserted rather than assumed, so a second `aria-hidden` element
 *  fails this helper loudly instead of silently retargeting every dot
 *  assertion below at something else. */
function dotTokens(container: HTMLElement): string[] {
  const decorative = container.querySelectorAll('[aria-hidden="true"]');
  expect(decorative).toHaveLength(1);
  return decorative[0].className.split(/\s+/);
}

/** The dot colour that belongs with each chip variant a status may use. This is
 *  the half of the invariant that is NOT derived from the entry under test —
 *  without it, enumerating the table only proves the row renders whatever the
 *  entry says, so an entry pairing `chip: "error"` with an amber dot passes by
 *  agreeing with itself. That is the exact bug this suite exists to prevent. */
const DOT_FOR_CHIP: Record<string, string> = {
  success: "bg-j-success",
  warning: "bg-j-warning",
  error: "bg-j-error",
};

// Derived from the table, NOT hand-written: a hand-written case list lets a
// fourth STATUS_PRESENTATION entry land with no assertion at all, which is the
// other way the amber-dot-beside-a-red-chip bug comes back. Enumerating the
// source makes the invariant structural.
test.each(Object.entries(STATUS_PRESENTATION))(
  "the dot and the chip agree for %s",
  (status, presentation) => {
    // The entry itself is severity-consistent...
    expect(DOT_FOR_CHIP[presentation.chip]).toBe(presentation.dot);

    // ...and the row renders what the entry says.
    const { container } = renderRow({ status });
    const tokens = dotTokens(container);
    for (const token of presentation.dot.split(/\s+/)) {
      expect(tokens).toContain(token);
    }
    expect(screen.getByText(presentation.label).className).toContain(
      CHIP_VARIANTS[presentation.chip],
    );
  },
);

// The fallback is not in the table, so it is asserted on its own. Amber, not
// red: the frontend cannot know a new status's severity, and rendering a benign
// one in red is the worse failure.
test("an unrecognised status gets the warning pair", () => {
  const { container } = renderRow({ status: "quarantined" });
  expect(dotTokens(container)).toContain("bg-j-warning");
  expect(screen.getByText("quarantined").className).toContain(CHIP_VARIANTS.warning);
});

test("a not-connected row's dot is an outline, not a colour", () => {
  const { container } = renderRow({
    configured: false,
    status: "unconfigured",
    source: "none",
  });
  const tokens = dotTokens(container);
  expect(tokens).toContain("border-t-muted");
  expect(tokens.some((token) => token.startsWith("bg-j-"))).toBe(false);
});

// Class-attribute order does not decide a cascade conflict — stylesheet order
// does, and `.text-j-error` is emitted BEFORE `.text-t-secondary`. Carrying both
// would render the destructive action grey.
test("Remove carries the error colour and not the default text colour", () => {
  renderRow({ source: "workspace" });
  const remove = screen.getByRole("button", { name: "Remove Anthropic" });
  expect(remove.className).toContain("text-j-error");
  expect(remove.className).not.toContain("text-t-secondary");
});

test("a truncated name stays reachable on hover", () => {
  renderRow();
  expect(screen.getByText("Anthropic")).toHaveAttribute("title", "Anthropic");
});

test("busy disables every action on a workspace-owned row", () => {
  renderRow({ source: "workspace" }, { busy: true });
  for (const name of ["Test Anthropic", "Edit Anthropic", "Remove Anthropic"]) {
    expect(screen.getByRole("button", { name })).toHaveProperty("disabled", true);
  }
});

test("busy disables Connect on a not-connected row", () => {
  renderRow(
    { configured: false, status: "unconfigured", source: "none" },
    { busy: true },
  );
  expect(screen.getByRole("button", { name: "Connect Anthropic" })).toHaveProperty(
    "disabled",
    true,
  );
});

test("busy disables Override and Test on an inherited row", () => {
  renderRow({ source: "env" }, { busy: true });
  for (const name of ["Test Anthropic", "Override Anthropic"]) {
    expect(screen.getByRole("button", { name })).toHaveProperty("disabled", true);
  }
});

test("Test and Remove call their own handlers", async () => {
  const { onTest, onRemove } = renderRow({ source: "workspace" });
  await userEvent.click(screen.getByRole("button", { name: "Test Anthropic" }));
  await userEvent.click(screen.getByRole("button", { name: "Remove Anthropic" }));
  expect(onTest).toHaveBeenCalledTimes(1);
  expect(onRemove).toHaveBeenCalledTimes(1);
});

// Its only behavioural claim: the rule is decoration, so it must not reach
// assistive tech. Its thickness and colour are cosmetics and are not pinned —
// a restyle should not have to edit a test.
test("the separator is hidden from assistive tech", () => {
  const { container } = render(<ProviderRowSeparator />);
  expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
});
