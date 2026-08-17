import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { ModelTab } from "./model-tab";
import type { ModelCatalog, ModelConfig } from "@/lib/types";

const catalog: ModelCatalog = {
  providers: {
    anthropic: [
      {
        model_id: "claude-sonnet-4-6",
        display_name: "Claude Sonnet 4.6",
        thinking_style: "anthropic_legacy",
        accepts_temperature: true,
        suggested_tier: "balanced",
      },
    ],
  },
};

const config: ModelConfig = {
  tiers: [
    {
      tier: "balanced",
      provider: "anthropic",
      model_id: "claude-sonnet-4-6",
      effort: "medium",
      max_tokens: 4096,
      temperature: null,
    },
  ],
  agent_overrides: [],
  providers: [{ provider: "anthropic", configured: true, status: "valid" }],
};

test("renders tier rows from config", async () => {
  render(
    <ModelTab open loading={false} catalog={catalog} config={config} onLoad={() => {}} />,
  );
  await waitFor(() => expect(screen.getByText(/balanced/i)).toBeInTheDocument());
});

test("calls onLoad on mount", () => {
  const onLoad = vi.fn();
  render(
    <ModelTab open loading={false} catalog={null} config={null} onLoad={onLoad} />,
  );
  expect(onLoad).toHaveBeenCalled();
});

test("fires onTestProvider when Test is clicked", async () => {
  const onTestProvider = vi.fn();
  render(
    <ModelTab
      open
      loading={false}
      catalog={catalog}
      config={config}
      onLoad={() => {}}
      onTestProvider={onTestProvider}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: /test/i }));
  expect(onTestProvider).toHaveBeenCalledWith("anthropic");
});

test("shows Configured for a configured provider", () => {
  render(
    <ModelTab open loading={false} catalog={catalog} config={config} onLoad={() => {}} />,
  );
  expect(screen.getByText(/configured/i)).toBeInTheDocument();
});

test("lists an env-backed configured provider in the tier dropdown", () => {
  // anthropic is reported configured (env-backed) with no explicit credential row.
  render(
    <ModelTab open loading={false} catalog={catalog} config={config} onLoad={() => {}} />,
  );
  const select = screen.getByLabelText("balanced provider") as HTMLSelectElement;
  const options = Array.from(select.options).map((o) => o.value);
  expect(options).toContain("anthropic");
  // And its provider card still surfaces the configured badge.
  expect(screen.getByText(/configured/i)).toBeInTheDocument();
});

test("keeps the binding's current provider in options when de-configured", () => {
  // Provider reported unconfigured, yet the tier is still bound to it: the
  // select must still list it so it never renders blank/mismatched.
  const deconfigured: ModelConfig = {
    ...config,
    providers: [{ provider: "anthropic", configured: false, status: "unconfigured" }],
  };
  render(
    <ModelTab open loading={false} catalog={catalog} config={deconfigured} onLoad={() => {}} />,
  );
  const select = screen.getByLabelText("balanced provider") as HTMLSelectElement;
  const options = Array.from(select.options).map((o) => o.value);
  expect(options).toContain("anthropic");
  expect(select.value).toBe("anthropic");
});
