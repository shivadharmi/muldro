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
import { catalog, config, deepClone } from "./providers-tab-fixtures";
import { ModelConfigProvider } from "../model-config-context";
import { fetchModelCatalog, fetchModelConfig } from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchModelCatalog).mockResolvedValue(deepClone(catalog));
  vi.mocked(fetchModelConfig).mockResolvedValue(deepClone(config));
});

function renderBare() {
  render(
    <ModelConfigProvider>
      <ProvidersTab />
    </ModelConfigProvider>,
  );
}

async function renderTab() {
  renderBare();
  // The load is fired from an effect; wait for the first row to exist.
  await screen.findByRole("button", { name: "Edit Anthropic" });
}

// A founder names the MODEL, not the vendor that hosts it.
test("searching by a model name matches its provider", async () => {
  await renderTab();
  await userEvent.type(screen.getByRole("searchbox"), "sonnet");

  expect(screen.getByRole("button", { name: "Edit Anthropic" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit OpenAI" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Connect Ollama" })).toBeNull();
});

// The haystack joins four fields, so a single-substring match would find nothing.
test("a multi-word search combines terms from different fields", async () => {
  await renderTab();
  await userEvent.type(screen.getByRole("searchbox"), "anthropic sonnet");

  expect(screen.getByRole("button", { name: "Edit Anthropic" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit OpenAI" })).toBeNull();
});

test("the search placeholder counts the real provider list", async () => {
  await renderTab();
  expect(screen.getByPlaceholderText("Search 4 providers")).toBeTruthy();
});

test("the segmented filter narrows the groups", async () => {
  await renderTab();
  expect(screen.getByRole("heading", { name: "Connected" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Available" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Available" }));
  expect(screen.queryByRole("heading", { name: "Connected" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Available" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Connected" }));
  expect(screen.getByRole("heading", { name: "Connected" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Available" })).toBeNull();
});

test("group headers carry the count of the rows they hold", async () => {
  await renderTab();
  expect(screen.getByTestId("provider-count-connected").textContent).toBe("2");
  expect(screen.getByTestId("provider-count-available").textContent).toBe("2");
});

// A row cannot enforce exclusivity about its siblings, so the tab owns it.
test("expansion is exclusive — opening a second row closes the first", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Connect Ollama" }));
  expect(screen.getByLabelText("Ollama base URL")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Edit Anthropic" }));
  expect(screen.queryByLabelText("Ollama base URL")).toBeNull();
  expect(screen.getByLabelText("Anthropic API key")).toBeTruthy();
});

test("Cancel collapses the expanded row", async () => {
  await renderTab();
  await userEvent.click(screen.getByRole("button", { name: "Connect Ollama" }));
  await userEvent.click(screen.getByRole("button", { name: "Cancel Ollama" }));
  expect(screen.queryByLabelText("Ollama base URL")).toBeNull();
});

// No catalog entry means no credential schema, so there is no form to render.
test("an uncatalogued provider renders without a credential form", async () => {
  await renderTab();
  expect(
    screen.getByRole("button", { name: "Remove legacy_vendor" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Connect legacy_vendor" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Test legacy_vendor" })).toBeNull();
});

test("the subtitle points app connections at /integrations", async () => {
  await renderTab();
  const link = screen.getByRole("link", { name: "Integrations" });
  expect(link.getAttribute("href")).toBe("/integrations");
});

// The context fires the same load and swallows the failure, so this tab is the
// only place that can report it.
test("a failed load is reported, not left spinning, and can be retried", async () => {
  vi.mocked(fetchModelCatalog).mockRejectedValue(new Error("catalog 500"));
  vi.mocked(fetchModelConfig).mockRejectedValue(new Error("config 500"));
  renderBare();

  await waitFor(() =>
    expect(addToast).toHaveBeenCalledWith(expect.any(String), "error"),
  );
  expect(await screen.findByText("Providers could not be loaded.")).toBeTruthy();
  expect(screen.queryByText("Loading providers…")).toBeNull();

  vi.mocked(fetchModelCatalog).mockResolvedValue(deepClone(catalog));
  vi.mocked(fetchModelConfig).mockResolvedValue(deepClone(config));
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("button", { name: "Edit Anthropic" })).toBeTruthy();
});
