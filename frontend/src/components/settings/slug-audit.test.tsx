/**
 * **A3, generalised: no bare slug reaches the screen, anywhere on this surface.**
 *
 * A3 was logged against one `<select>` that announced `google_genai`, and for a
 * while it was fixed there and nowhere else — so the same uncatalogued provider
 * read "Google genai" in the model picker and `google_genai` in the tier card
 * and the provider row. Four call sites were then corrected by hand. This test
 * exists so the FIFTH does not need finding by hand: it renders every tab, walks
 * everything a screen reader would announce, and fails on anything shaped like
 * `lower_snake_case`.
 *
 * It is the same move `design-tokens.test.ts` makes for dead tokens — catch the
 * class, not the instances. The two failure modes are cousins: a slug and a
 * nonexistent token are both just strings, and both survive `tsc` and `eslint`
 * because nothing in the type system knows one from a sentence.
 *
 * **The fixtures are the guard.** Every value the server could hand us that IS
 * a slug is seeded snake_case on purpose — an uncatalogued `legacy_vendor`, a
 * `google_genai` provider, `anthropic_adaptive` and `openai_effort` thinking
 * styles, a `pending_review` trust level the client has no label for. A sweep
 * over data that contains no underscores would pass forever and prove nothing.
 *
 * **What it does not read:** `id`, `class`, `data-*`, `for`, `name`,
 * `aria-labelledby`, `aria-controls`, `aria-activedescendant`. Those are machine
 * identifiers, they are supposed to carry slugs, and reading them would make the
 * guard permanently red for the wrong reason.
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach, afterEach } from "vitest";

import type {
  CatalogModel,
  ModelBinding,
  ModelCatalog,
  ModelConfig,
  ProviderStatus,
  TrustDashboardEntry,
} from "@/lib/types";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { email: "founder@example.com", display_name: "Founder" },
    logout: vi.fn(),
  }),
}));
vi.mock("@/lib/theme", () => ({
  useTheme: () => ({ theme: "system", resolved: "dark", setTheme: vi.fn() }),
}));

// ── The bait ───────────────────────────────────────────────────────────────

function model(over: Partial<CatalogModel> = {}): CatalogModel {
  return {
    provider: "google_genai",
    model_id: "gemini-3-pro",
    display_name: "Gemini 3 Pro",
    thinking_style: "anthropic_adaptive",
    accepts_temperature: true,
    suggested_tier: "reasoning",
    context_window: 1_000_000,
    input_cost_per_1k: 0.004,
    output_cost_per_1k: 0.02,
    supports_prompt_cache: true,
    ...over,
  };
}

const CATALOG: ModelCatalog = {
  providers: [
    {
      provider: "google_genai",
      display_name: "Google Gemini",
      auth_kind: "api_key",
      credential_fields: [
        { key: "api_key", label: "API key", kind: "secret", required: true, placeholder: null },
      ],
      model_count: 2,
      docs_url: null,
    },
  ],
  models: [
    model(),
    model({ model_id: "o5-pro", display_name: "o5 Pro", thinking_style: "openai_effort" }),
    // A thinking style `model-picker-catalog.ts` has NO label for, so the
    // palette's chip has to fall through to the humaniser to render at all.
    model({ model_id: "grok-5", display_name: "Grok 5", thinking_style: "xai_reasoning" }),
  ],
  // An agent whose name is snake_case and whose display name is not, so the
  // override list has something to get wrong.
  agents: [{ name: "risk_assessor", display_name: "Risk assessor", tier: "balanced" }],
};

function status(provider: string, over: Partial<ProviderStatus> = {}): ProviderStatus {
  return {
    provider,
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

function tier(scopeKey: string): ModelBinding {
  return {
    scope_type: "tier",
    scope_key: scopeKey,
    provider: "google_genai",
    model_id: "gemini-3-pro",
    effort: "medium",
    max_tokens: 4096,
    temperature: null,
  };
}

const CONFIG: ModelConfig = {
  tiers: [
    tier("reasoning"),
    tier("balanced"),
    // Bound to a provider the catalog does not list, so `binding-fields.tsx`
    // must render its provider fallback rather than a catalog display name.
    { ...tier("fast"), provider: "legacy_vendor", model_id: "ancient-1" },
  ],
  agent_overrides: [
    { ...tier("risk_assessor"), scope_type: "agent" },
    // An override for an agent the catalog does not list — `agent-overrides.tsx`
    // has only the scope key to label the row with.
    { ...tier("memory_writer"), scope_type: "agent" },
  ],
  providers: [
    status("google_genai"),
    // The stray the catalog no longer lists — the site that used to print the
    // raw slug in three different places.
    status("legacy_vendor", { catalogued: false, source: "none", configured: false, status: "unconfigured" }),
  ],
  warnings: [],
};

const TRUST: { capabilities: TrustDashboardEntry[] } = {
  capabilities: [
    {
      capability: "email.send",
      family: "outbound_comms",
      // A level `trust-constants.ts` has no label for: the `??` fallback is the
      // hole this seeds.
      trust_level: "pending_review",
      ceiling: "autonomous",
      risk_levels: [
        {
          risk_level: "blast_radius",
          trust_level: "first_use",
          approved_count: 2,
          rejected_count: 0,
          graduation_progress: {
            current: 2,
            target: 3,
            next_level: "learning",
            percentage: 66,
            blocked_by_rejections: false,
          },
        },
      ],
    },
  ],
};

vi.mock("@/lib/api", () => ({
  fetchPolicyMode: vi.fn(),
  setPolicyMode: vi.fn(),
  fetchWorkspaceDefaultPermissionMode: vi.fn(),
  setWorkspaceDefaultPermissionMode: vi.fn(),
  fetchBudget: vi.fn(),
  updateBudgetLimit: vi.fn(),
  fetchTrustDashboard: vi.fn(),
  setTrustCeiling: vi.fn(),
  resetTrust: vi.fn(),
  fetchModelCatalog: vi.fn(),
  fetchModelConfig: vi.fn(),
  saveModelConfig: vi.fn(),
  saveProviderCredential: vi.fn(),
  testProviderKey: vi.fn(),
  deleteProviderKey: vi.fn(),
}));

import {
  fetchBudget,
  fetchModelCatalog,
  fetchModelConfig,
  fetchPolicyMode,
  fetchTrustDashboard,
  fetchWorkspaceDefaultPermissionMode,
} from "@/lib/api";
import { SettingsModal } from "./settings-modal";
import { SETTINGS_TABS } from "./settings-rail";
import { useSettingsModalStore } from "@/stores/settings-modal-store";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchPolicyMode).mockResolvedValue({ mode: "approval_required" });
  vi.mocked(fetchWorkspaceDefaultPermissionMode).mockResolvedValue({
    default_permission_mode: "auto",
  });
  vi.mocked(fetchBudget).mockResolvedValue({ daily_limit_usd: 25 });
  vi.mocked(fetchTrustDashboard).mockResolvedValue(TRUST);
  vi.mocked(fetchModelCatalog).mockResolvedValue(CATALOG);
  vi.mocked(fetchModelConfig).mockResolvedValue(CONFIG);
});

afterEach(cleanup);

// ── The sweep ──────────────────────────────────────────────────────────────

/** `lower_snake_case`, whole string. Anchored on both ends so a sentence that
 *  merely CONTAINS an identifier (a server message quoting one, a URL) is not
 *  swept up — this is about a slug standing in for a name, not about prose. */
const SLUG = /^[a-z0-9]+(_[a-z0-9]+)+$/;

/** Attributes an assistive technology reads out. Everything omitted here —
 *  `id`, `class`, `data-*`, `for`, `aria-labelledby`, `aria-controls`,
 *  `aria-activedescendant` — is a machine identifier and is SUPPOSED to be a
 *  slug. */
const SPOKEN = ["aria-label", "aria-description", "placeholder", "title", "alt"];

function announcedStrings(root: Element): string[] {
  const out: string[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n.textContent?.trim();
    if (text) out.push(text);
  }
  for (const el of [root, ...Array.from(root.querySelectorAll("*"))]) {
    for (const attr of SPOKEN) {
      const value = el.getAttribute(attr)?.trim();
      if (value) out.push(value);
    }
    // A control's displayed value is announced with it, and React sets it as a
    // property rather than as an attribute.
    if (el instanceof HTMLInputElement || el instanceof HTMLSelectElement) {
      if (el.value.trim()) out.push(el.value.trim());
    }
  }
  return out;
}

const slugsIn = (root: Element): string[] =>
  Array.from(new Set(announcedStrings(root).filter((s) => SLUG.test(s))));

/** Let every tab's fetches resolve and their state land. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function dialog(): HTMLElement {
  return screen.getByRole("dialog");
}

test.each(SETTINGS_TABS.map((t) => t.key))(
  "the %s tab announces no bare slug",
  async (key) => {
    useSettingsModalStore.setState({ open: true, activeTab: key });
    render(<SettingsModal />);
    await settle();

    // The sweep is worthless against an empty panel, so prove the tab rendered.
    expect(dialog().textContent?.length ?? 0).toBeGreaterThan(40);
    expect(slugsIn(dialog())).toEqual([]);
  },
);

test("the model picker announces no bare slug either", async () => {
  useSettingsModalStore.setState({ open: true, activeTab: "model" });
  render(<SettingsModal />);
  await settle();

  await userEvent.click(screen.getAllByLabelText(/^Model/)[0]);
  const palette = screen.getByRole("dialog", { name: /choose a model/i });

  // The palette is where A3 was first logged, and where the thinking-style
  // chips render — `anthropic_adaptive` and `openai_effort` both reach it.
  expect(palette.textContent).toContain("Adaptive");
  expect(slugsIn(palette)).toEqual([]);
});

test("the per-agent overrides list announces no bare slug", async () => {
  // Collapsed by default, so the tab sweep above never reaches it — and an
  // override for an agent the catalog does not list has nothing but its scope
  // key to render.
  useSettingsModalStore.setState({ open: true, activeTab: "model" });
  render(<SettingsModal />);
  await settle();

  await userEvent.click(screen.getByRole("button", { name: /per-agent overrides/i }));
  expect(screen.getByText("Memory writer")).toBeTruthy();
  expect(slugsIn(dialog())).toEqual([]);
});

test("the sweep would catch a slug that leaked", async () => {
  // A guard that cannot fail is worse than no guard. This is the shape of every
  // defect the sweep exists for: a label position holding an identifier.
  useSettingsModalStore.setState({ open: true, activeTab: "providers" });
  render(<SettingsModal />);
  await settle();

  const planted = document.createElement("span");
  planted.textContent = "google_genai";
  dialog().append(planted);
  expect(slugsIn(dialog())).toEqual(["google_genai"]);

  planted.textContent = "";
  planted.setAttribute("aria-label", "legacy_vendor");
  expect(slugsIn(dialog())).toEqual(["legacy_vendor"]);
});
