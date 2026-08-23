import { render } from "@testing-library/react";
import { expect, test, vi, afterEach } from "vitest";

vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock("@/lib/api", () => ({
  fetchModelCatalog: vi.fn().mockResolvedValue({ providers: [], models: [], agents: [] }),
  fetchModelConfig: vi.fn().mockResolvedValue({
    tiers: [],
    agent_overrides: [],
    providers: [],
    warnings: [],
  }),
  saveModelConfig: vi.fn(),
  saveProviderCredential: vi.fn(),
  testProviderKey: vi.fn(),
  deleteProviderKey: vi.fn(),
}));

import {
  useModelCatalog,
  useModelConfigContext,
  useProviderCounts,
} from "./model-config-context";

/**
 * These hooks throw on a missing provider ON PURPOSE. `useProviderCounts` in
 * particular distinguishes `undefined` ("no provider above me") from `null`
 * ("nothing loaded yet"), and a future "helpful" default would collapse the two
 * back together — a component rendered outside the provider would then look
 * exactly like one waiting on a fetch, forever, with nothing on screen wrong.
 * Without these tests that regression is silent, so they guard the throw
 * itself, not merely the happy path.
 */
const consoleError = vi
  .spyOn(console, "error")
  .mockImplementation(() => {});

afterEach(() => consoleError.mockClear());

function CountsConsumer() {
  useProviderCounts();
  return null;
}

function ConfigConsumer() {
  useModelConfigContext();
  return null;
}

function CatalogConsumer() {
  useModelCatalog();
  return null;
}

test("useProviderCounts outside the provider throws", () => {
  expect(() => render(<CountsConsumer />)).toThrow(/ModelConfigProvider/);
});

test("useModelConfigContext outside the provider throws", () => {
  expect(() => render(<ConfigConsumer />)).toThrow(/ModelConfigProvider/);
});

test("useModelCatalog outside the provider throws", () => {
  expect(() => render(<CatalogConsumer />)).toThrow(/ModelConfigProvider/);
});
