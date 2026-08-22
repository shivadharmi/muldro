import { render, screen, waitFor } from "@testing-library/react";
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
  catalog,
  config,
  connected,
  deepClone,
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
  vi.mocked(fetchModelCatalog).mockResolvedValue(deepClone(catalog));
  vi.mocked(fetchModelConfig).mockResolvedValue(deepClone(config));
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
    ...deepClone(config),
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

test("Remove does not block a provider nothing depends on", async () => {
  deleteResolves("anthropic");
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove Anthropic" }));
  await waitFor(() => expect(deleteProviderKey).toHaveBeenCalledWith("anthropic"));
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

// A banner still standing for a row whose neighbour just deleted is answering
// for a screen that no longer exists.
test("an open confirmation is cleared by a dependency-free removal elsewhere", async () => {
  deleteResolves("anthropic");
  await renderTab();

  await userEvent.click(screen.getByRole("button", { name: "Remove OpenAI" }));
  await userEvent.click(screen.getByRole("button", { name: "Remove Anthropic" }));
  await waitFor(() => expect(deleteProviderKey).toHaveBeenCalledWith("anthropic"));
  expect(screen.queryByRole("alertdialog")).toBeNull();
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
  await waitFor(() =>
    expect(addToast).toHaveBeenCalledWith(
      expect.stringContaining("The balanced tier has no configured provider."),
      "warning",
    ),
  );
});
