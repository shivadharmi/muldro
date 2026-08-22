import { act, render, screen, waitFor } from "@testing-library/react";
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

import { ProvidersTab } from "./providers-tab";
import {
  connected,
  makeCatalog,
  makeConfig,
  notConnected,
  rowAnchor,
} from "./providers-tab-fixtures";
import { ModelConfigProvider } from "../model-config-context";
import {
  deleteProviderKey,
  fetchModelCatalog,
  fetchModelConfig,
} from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchModelCatalog).mockResolvedValue(makeCatalog());
  vi.mocked(fetchModelConfig).mockResolvedValue(makeConfig());
});

async function renderTab() {
  render(
    <ModelConfigProvider>
      <ProvidersTab />
    </ModelConfigProvider>,
  );
  await screen.findByRole("button", { name: "Edit Anthropic" });
}

function deleteResolves(provider: string) {
  vi.mocked(deleteProviderKey).mockResolvedValue({
    status: notConnected(provider),
    orphaned_bindings: [],
  });
}

// The dependency set is computed from the config ON SCREEN, because
// `orphaned_bindings` only exists once the credential is already gone.
test("Remove asks first when bindings depend on the provider", async () => {
  deleteResolves("openai");
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  expect(deleteProviderKey).not.toHaveBeenCalled();
  expect(screen.getByRole("alertdialog").textContent).toContain(
    "Removing OpenAI breaks the fast tier",
  );

  await userEvent.click(screen.getByRole("button", { name: "Remove anyway" }));
  await waitFor(() => expect(deleteProviderKey).toHaveBeenCalledWith("openai"));
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

// Detached from its row it was invisible when scrolled away, and — preceding
// every group in DOM order — unreachable by a forward Tab from the button that
// opened it.
test("the confirmation renders beneath the row that raised it", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));

  const dialog = screen.getByRole("alertdialog");
  const position = rowAnchor("openai").compareDocumentPosition(dialog);
  expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(rowAnchor("anthropic").contains(dialog)).toBe(false);
});

// An alertdialog owns focus; `alert` would only announce and leave it behind.
test("focus enters the confirmation and returns to the row on dismiss", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Keep it" }),
  );

  await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
  await waitFor(() => expect(document.activeElement).toBe(rowAnchor("openai")));
});

// The row survives a delete but MOVES from the Connected card to the Available
// one, unmounting whatever held focus a second time.
test("focus is not lost when the confirmation is confirmed", async () => {
  deleteResolves("openai");
  await renderTab();
  // Only the post-delete refetch sees OpenAI disconnected.
  vi.mocked(fetchModelConfig).mockResolvedValue({
    ...makeConfig(),
    providers: [
      connected("anthropic"),
      notConnected("openai"),
      notConnected("ollama"),
      notConnected("legacy_vendor", false),
    ],
  });

  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  await userEvent.click(screen.getByRole("button", { name: "Remove anyway" }));
  await screen.findByRole("button", { name: "Connect OpenAI" });
  await waitFor(() => expect(document.activeElement).toBe(rowAnchor("openai")));
});

// The settings shell closes the WHOLE modal on an unhandled Escape.
test("Escape cancels the confirmation and is not left for the shell", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));

  const seen: KeyboardEvent[] = [];
  const listener = (event: KeyboardEvent) => seen.push(event);
  document.addEventListener("keydown", listener);
  await userEvent.keyboard("{Escape}");
  document.removeEventListener("keydown", listener);

  expect(seen.at(-1)?.defaultPrevented).toBe(true);
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(deleteProviderKey).not.toHaveBeenCalled();
});

// It must never BLOCK: a credential the founder cannot revoke is a security
// problem, so the confirmation is a sentence and a second click, not a veto.
test("the confirmation can be declined without deleting", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(deleteProviderKey).not.toHaveBeenCalled();
});

// Every removal is confirmed. A key with no dependent binding is still
// destroyed by that click and cannot be read back — only the sentence differs.
test("a provider nothing depends on is confirmed in plainer words", async () => {
  deleteResolves("anthropic");
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove Anthropic" }));
  expect(deleteProviderKey).not.toHaveBeenCalled();
  const text = screen.getByRole("alertdialog").textContent ?? "";
  expect(text).toContain("Remove the Anthropic key?");
  expect(text).not.toContain("breaks");

  await userEvent.click(screen.getByRole("button", { name: "Remove anyway" }));
  await waitFor(() => expect(deleteProviderKey).toHaveBeenCalledWith("anthropic"));
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

// One confirmation at a time, and it belongs to the row it is beneath — asking
// about a second provider MOVES it rather than leaving two questions open.
test("a second Remove moves the confirmation to that row", async () => {
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  await userEvent.click(screen.getByRole("button", { name: "Remove Anthropic" }));

  const dialogs = screen.getAllByRole("alertdialog");
  expect(dialogs).toHaveLength(1);
  expect(dialogs[0].textContent).toContain("Remove the Anthropic key?");
  const position = rowAnchor("anthropic").compareDocumentPosition(dialogs[0]);
  expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(deleteProviderKey).not.toHaveBeenCalled();
});

// The post-delete truth still gets reported, even though the confirmation was
// answered from the config on screen.
test("orphaned bindings reported by the delete are surfaced", async () => {
  vi.mocked(deleteProviderKey).mockResolvedValue({
    status: notConnected("anthropic"),
    orphaned_bindings: [
      {
        scope_type: "tier",
        scope_key: "balanced",
        provider: "anthropic",
        code: "provider_not_configured",
        message: "The balanced tier has no configured provider.",
      },
    ],
  });
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove Anthropic" }));
  await userEvent.click(screen.getByRole("button", { name: "Remove anyway" }));
  await waitFor(() =>
    expect(addToast).toHaveBeenCalledWith(
      expect.stringContaining("The balanced tier has no configured provider."),
      "warning",
    ),
  );
});

// The half of the restore that protects the founder who moved on. Without this,
// a "simplification" back to an unconditional `.focus()` passes everything else.
test("a slow delete does not steal focus back from where the founder moved", async () => {
  let settle: (() => void) | undefined;
  vi.mocked(deleteProviderKey).mockReturnValue(
    new Promise((resolve) => {
      settle = () =>
        resolve({ status: notConnected("openai"), orphaned_bindings: [] });
    }),
  );
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  await userEvent.click(screen.getByRole("button", { name: "Remove anyway" }));

  // The founder walks off to another row while the delete is still in flight.
  const elsewhere = screen.getByRole("button", { name: "Test Anthropic" });
  elsewhere.focus();
  expect(document.activeElement).toBe(elsewhere);

  await act(async () => {
    settle?.();
  });
  await waitFor(() => expect(deleteProviderKey).toHaveBeenCalledWith("openai"));
  expect(document.activeElement).toBe(elsewhere);
});
