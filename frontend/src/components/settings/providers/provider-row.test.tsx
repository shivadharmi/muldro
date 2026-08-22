import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import { ProviderRow } from "./provider-row";
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
  render(
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
  return { onToggle, onTest, onRemove };
}

test("a workspace-owned credential offers Test, Edit and Remove", () => {
  renderRow({ source: "workspace" });
  expect(screen.getByRole("button", { name: "Test" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove" })).toBeTruthy();
});

// DELETE removes the workspace credential row and nothing else, so an inherited
// credential must not offer a button that appears to work and changes nothing.
test("Remove is absent for an env-provided credential", () => {
  renderRow({ source: "env" });
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
});

test("Remove is absent for a deployment-default credential", () => {
  renderRow({ source: "default" });
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
});

test("an env-sourced provider offers Override, not Edit", () => {
  renderRow({ source: "env" });
  expect(screen.getByRole("button", { name: "Override" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

test("a not-connected provider offers only Connect", () => {
  renderRow({ configured: false, status: "unconfigured", source: "none" });
  expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Test" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
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
  expect(screen.getByRole("button", { name: "Remove" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Test" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
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
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.getByText("credential form")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
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

test("a reason renders as a chip", () => {
  renderRow({}, { reason: "Needed by the Fast tier" });
  expect(screen.getByText("Needed by the Fast tier")).toBeTruthy();
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

test("busy disables every action", () => {
  renderRow({ source: "workspace" }, { busy: true });
  for (const name of ["Test", "Edit", "Remove"]) {
    expect(screen.getByRole("button", { name })).toHaveProperty("disabled", true);
  }
});

test("Test and Remove call their own handlers", async () => {
  const { onTest, onRemove } = renderRow({ source: "workspace" });
  await userEvent.click(screen.getByRole("button", { name: "Test" }));
  await userEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(onTest).toHaveBeenCalledTimes(1);
  expect(onRemove).toHaveBeenCalledTimes(1);
});
