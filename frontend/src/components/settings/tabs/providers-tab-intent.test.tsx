import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/api", () => ({
  fetchModelCatalog: vi.fn(),
  fetchModelConfig: vi.fn(),
  saveModelConfig: vi.fn(),
  saveProviderCredential: vi.fn(),
  testProviderKey: vi.fn(),
  deleteProviderKey: vi.fn(),
}));

import { fetchModelCatalog, fetchModelConfig } from "@/lib/api";
import { useSettingsModalStore } from "@/stores/settings-modal-store";
import { ModelConfigProvider } from "../model-config-context";
import { ProvidersTab } from "./providers-tab";
import { makeCatalog, makeConfig, rowAnchor } from "./providers-tab-fixtures";

const REASON = "Needed by the Fast tier";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchModelCatalog).mockResolvedValue(makeCatalog());
  vi.mocked(fetchModelConfig).mockResolvedValue(makeConfig());
  useSettingsModalStore.setState({
    open: true,
    activeTab: "providers",
    pendingProvider: null,
  });
});

/** Mounts the tab the way the shell does — a fresh mount per visit, which is
 *  what makes the intent a one-shot rather than standing state. */
function mountTab() {
  return render(
    <ModelConfigProvider>
      <ProvidersTab />
    </ModelConfigProvider>,
  );
}

async function mountAndSettle() {
  const view = mountTab();
  await screen.findByRole("button", { name: "Edit Anthropic" });
  return view;
}

function intend(provider: string, reason = REASON) {
  useSettingsModalStore.getState().openProviderFor(provider, reason);
}

test("an intent expands its row and says why it was opened", async () => {
  intend("ollama");
  await mountAndSettle();

  // The primary action reads Cancel only while the row is open, so this is the
  // expansion itself and not a guess from the chip beside it.
  expect(screen.getByRole("button", { name: "Cancel Ollama" })).toBeTruthy();
  expect(screen.getByLabelText("Ollama base URL")).toBeTruthy();
  expect(within(rowAnchor("ollama")).getByText(REASON)).toBeTruthy();
});

// The reason belongs to the row that was opened FOR the founder. A second row
// opened by hand must not inherit it.
test("only the intended row carries the reason", async () => {
  intend("ollama");
  await mountAndSettle();

  await userEvent.click(screen.getByRole("button", { name: "Edit Anthropic" }));
  expect(screen.queryByText(REASON)).toBeNull();
  expect(screen.queryByLabelText("Ollama base URL")).toBeNull();
});

// An intent that survived its trip would re-open a row the founder has already
// dealt with — possibly one they have since connected.
test("the intent is consumed, so returning to the tab re-opens nothing", async () => {
  intend("ollama");
  const first = await mountAndSettle();
  expect(useSettingsModalStore.getState().pendingProvider).toBeNull();

  first.unmount();
  await mountAndSettle();

  expect(screen.getByRole("button", { name: "Connect Ollama" })).toBeTruthy();
  expect(screen.queryByLabelText("Ollama base URL")).toBeNull();
  expect(screen.queryByText(REASON)).toBeNull();
});

/**
 * The visual landing is not the landing. The founder arrives with focus on
 * `<body>` — the button they pressed unmounted with its tab — and a keyboard or
 * screen-reader user would otherwise be nowhere near the row, with the reason
 * chip a bare `<span>` they never reach.
 *
 * Deferred rather than done on mount: the rows do not exist until the config
 * fetch lands, so a focus call at mount would find nothing.
 */
test("focus lands on the row an intent named", async () => {
  intend("ollama");
  await mountAndSettle();

  await waitFor(() => expect(document.activeElement).toBe(rowAnchor("ollama")));
});

/**
 * `arrivedAt` is a fact about how the mount BEGAN, and it must not be re-derived
 * from `expanded?.reason` — the obvious collapse for the next reader, since the
 * two sit side by side. Every other focus test would survive that change; this
 * one is the reason it cannot ship, because a reasonless intent is legal
 * (`openProviderFor(provider)`) and would silently stop moving focus.
 */
test("a reasonless intent still moves focus to its row", async () => {
  useSettingsModalStore.getState().openProviderFor("ollama");
  await mountAndSettle();

  await waitFor(() => expect(document.activeElement).toBe(rowAnchor("ollama")));
});

/**
 * The failure path is where WCAG 2.4.3 actually bites: the founder pressed a
 * button that then unmounted, the load failed, there is no row to land on, and
 * focus is sitting on `<body>` inside a focus trap — from which the next Tab
 * restarts at the top. The panel itself is then the entry point; it holds the
 * message and the retry.
 */
test("a failed load still gives the trap something to hold", async () => {
  vi.mocked(fetchModelCatalog).mockRejectedValue(new Error("catalog 500"));
  vi.mocked(fetchModelConfig).mockRejectedValue(new Error("config 500"));
  intend("ollama");

  const { container } = mountTab();
  await screen.findByText("Providers could not be loaded.");

  await waitFor(() => expect(document.activeElement).not.toBe(document.body));
  expect(container.contains(document.activeElement)).toBe(true);
});

test("the reason names whichever tier sent the founder", async () => {
  intend("ollama", "Needed by the Reasoning tier");
  await mountAndSettle();

  expect(
    within(rowAnchor("ollama")).getByText("Needed by the Reasoning tier"),
  ).toBeTruthy();
});

// An uncatalogued provider has no credential schema, so `ProviderRow` withholds
// the body — and the founder still has to be told why they were sent here. The
// row expands and states the reason; it just has nothing to fill in.
test("an intent naming an uncatalogued provider still opens and explains", async () => {
  intend("legacy_vendor");
  await mountAndSettle();

  const row = rowAnchor("legacy_vendor");
  expect(within(row).getByText(REASON)).toBeTruthy();
  // Expanded, visibly: the accent rail and tint §9.8 marks an open row with.
  expect(row.firstElementChild?.className).toContain("border-j-primary");
  // …and nothing to type into, because the catalog lists no fields for it.
  expect(within(row).queryByRole("textbox")).toBeNull();
  expect(within(row).getByRole("button", { name: "Remove legacy_vendor" })).toBeTruthy();
});

// The tab is mounted by the shell's tab switch, and nothing else may re-consume
// what it took: a stale intent left in the store is an expansion waiting to
// ambush the next visit.
test("a manual visit with no intent expands nothing", async () => {
  await mountAndSettle();

  expect(screen.getByRole("button", { name: "Connect Ollama" })).toBeTruthy();
  expect(screen.queryByLabelText("Ollama base URL")).toBeNull();
});
