import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";

import type { ModelConfig } from "@/lib/types";

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

import { fetchModelCatalog, fetchModelConfig, saveModelConfig } from "@/lib/api";
import { useSettingsModalStore } from "@/stores/settings-modal-store";
import {
  card,
  catalog,
  config,
  echoServer,
  mountTab,
  nudge,
  openOverrides,
  renderTab,
  saveButton,
  tier,
} from "./model-tab-fixtures";

beforeEach(() => {
  vi.clearAllMocks();
  useSettingsModalStore.setState({ activeTab: "model", pendingProvider: null });
});

/** Fast bound to Groq, which resolves no credential — the state §9.6 renders as
 *  a consequence and offers `Connect Groq` for. The binding has to name the
 *  warned provider or `TierCard` ignores the warning as already answered. */
const warnedConfig: ModelConfig = config({
  tiers: [
    tier("reasoning", "claude-opus-4-5"),
    tier("balanced", "claude-sonnet-4-6"),
    { ...tier("fast", "llama-3.3-70b"), provider: "groq" },
  ],
  warnings: [
    {
      scope_type: "tier",
      scope_key: "fast",
      provider: "groq",
      code: "provider_not_configured",
      message: "Groq is not connected.",
    },
  ],
});

// The slug alone would land the founder on a list of providers with nothing
// said about which one they came for — the defect this wiring closes.
test("Connect on a warned tier opens Providers FOR that provider", async () => {
  await renderTab(warnedConfig);
  await userEvent.click(screen.getByRole("button", { name: "Connect Groq" }));

  const state = useSettingsModalStore.getState();
  expect(state.activeTab).toBe("providers");
  // The reason names the TIER that sent them, not the provider they can see.
  expect(state.pendingProvider).toEqual({
    provider: "groq",
    reason: "Needed by the Fast tier",
  });
});

// The picker's footer has no provider in mind, so it must not invent one.
test("Browse all providers switches tab and names no provider", async () => {
  await renderTab();
  await userEvent.click(within(card("Fast")).getByLabelText(/^Model/));
  await userEvent.click(
    screen.getByRole("button", { name: /browse all providers/i }),
  );

  const state = useSettingsModalStore.getState();
  expect(state.activeTab).toBe("providers");
  expect(state.pendingProvider).toBeNull();
});

test("F2: one save affordance, and it persists tiers AND overrides together", async () => {
  echoServer();
  await renderTab();
  await openOverrides();

  // Both surfaces are on screen — tiers above, overrides expanded below — and
  // between them there is exactly one Save. The deleted second button ("Save
  // overrides") sent this same whole-draft body.
  expect(screen.getAllByRole("button", { name: /^save/i })).toHaveLength(1);

  await userEvent.selectOptions(
    screen.getByLabelText(/add an override/i),
    "presenter",
  );
  await userEvent.click(screen.getByRole("button", { name: /^add override$/i }));
  await nudge("Balanced");

  await userEvent.click(saveButton());

  await waitFor(() => expect(saveModelConfig).toHaveBeenCalledTimes(1));
  const body = vi.mocked(saveModelConfig).mock.calls[0][0];
  expect(body.tiers).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ scope_key: "balanced", max_tokens: 40960 }),
    ]),
  );
  expect(body.agent_overrides).toEqual([
    expect.objectContaining({ scope_type: "agent", scope_key: "presenter" }),
  ]);
});

test("F1: the picker sets provider and model_id together; no provider control exists", async () => {
  echoServer();
  await renderTab();

  // The pair that made F1 possible is gone: nothing on this tab is labelled
  // "provider", so nothing can rewrite one key as a side effect of the other.
  expect(screen.queryByLabelText(/provider/i)).toBeNull();

  await userEvent.click(within(card("Reasoning")).getByLabelText(/^Model/));
  await userEvent.click(screen.getByRole("option", { name: /Llama 3\.3 70B/ }));

  expect(
    within(card("Reasoning")).getByLabelText(/^Model Llama 3\.3 70B/),
  ).toBeInTheDocument();

  await userEvent.click(saveButton());
  await waitFor(() => expect(saveModelConfig).toHaveBeenCalledTimes(1));
  expect(vi.mocked(saveModelConfig).mock.calls[0][0].tiers).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        scope_key: "reasoning",
        provider: "groq",
        model_id: "llama-3.3-70b",
      }),
    ]),
  );
});

test("a 422 lands on the refused card, not in a toast", async () => {
  vi.mocked(saveModelConfig).mockRejectedValue({
    bindRejections: [
      {
        scope_type: "tier",
        scope_key: "fast",
        provider: "groq",
        code: "provider_not_configured",
        message: "Groq is not connected, so Fast was not saved.",
      },
    ],
  });
  await renderTab();
  await nudge("Fast");
  await userEvent.click(saveButton());

  await waitFor(() =>
    expect(
      within(card("Fast")).getByText(/Groq is not connected, so Fast/),
    ).toBeInTheDocument(),
  );
  // The verdict names a binding; a toast could not, so it must not duplicate it.
  expect(addToast).not.toHaveBeenCalled();
  // …and it is on the OFFENDING card only.
  expect(
    within(card("Balanced")).queryByText(/not connected/),
  ).not.toBeInTheDocument();
});

test("an override can be added and removed, and the add flow cannot overwrite one", async () => {
  echoServer();
  await renderTab();

  expect(
    screen.getByRole("button", { name: /per-agent overrides\s*0 active/i }),
  ).toBeInTheDocument();
  await openOverrides();

  await userEvent.selectOptions(
    screen.getByLabelText(/add an override/i),
    "planner",
  );
  await userEvent.click(screen.getByRole("button", { name: /^add override$/i }));

  // Seeded from the tier the agent rides on, so the new override starts runnable.
  expect(
    within(card("Planner")).getByLabelText(/^Model Claude Opus 4\.5/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /per-agent overrides\s*1 active/i }),
  ).toBeInTheDocument();

  // `upsertBinding` REPLACES silently, so an agent that already has an override
  // must not be offerable a second time.
  const select = screen.getByLabelText(/add an override/i);
  expect(
    within(select).queryByRole("option", { name: "Planner" }),
  ).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
  expect(screen.queryByRole("region", { name: "Planner" })).toBeNull();
  expect(
    within(select).getByRole("option", { name: "Planner" }),
  ).toBeInTheDocument();
});

test("a failed load says so and offers a retry, never 'no tiers'", async () => {
  // `config === null` and `tiers.length === 0` are indistinguishable from the
  // tier list alone, so one message would report a fetch failure as a fact
  // about the workspace — a founder would go looking for missing tiers.
  vi.mocked(fetchModelCatalog).mockResolvedValue(catalog());
  vi.mocked(fetchModelConfig).mockRejectedValue(new Error("network"));
  mountTab();

  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      /could not load the model configuration/i,
    ),
  );
  expect(screen.queryByText(/no tiers are configured/i)).toBeNull();
  expect(screen.getByRole("button", { name: /^retry$/i })).toBeInTheDocument();

  // The hook's guard resets on failure, so the retry is a real second request.
  vi.mocked(fetchModelConfig).mockResolvedValue(config());
  await userEvent.click(screen.getByRole("button", { name: /^retry$/i }));
  await waitFor(() => expect(card("Reasoning")).toBeInTheDocument());
});
