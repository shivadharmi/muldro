import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import type { Mock } from "vitest";

import { ProviderCredentialForm } from "./provider-credential-form";
import type {
  CatalogProvider,
  CredentialFields,
  CredentialFieldSpec,
  ProviderStatus,
} from "@/lib/types";

function field(over: Partial<CredentialFieldSpec> & { key: string }): CredentialFieldSpec {
  return { label: over.key, kind: "text", required: false, placeholder: null, ...over };
}

function catalogProvider(
  provider: string,
  credential_fields: CredentialFieldSpec[],
): CatalogProvider {
  return {
    provider,
    display_name: provider,
    auth_kind: "api_key",
    credential_fields,
    model_count: 3,
    docs_url: null,
  };
}

function providerStatus(over: Partial<ProviderStatus> = {}): ProviderStatus {
  return {
    provider: "anthropic",
    configured: false,
    status: "unknown",
    source: "none",
    base_url: null,
    extra_config_public: {},
    extra_config_secret_keys: [],
    catalogued: true,
    ...over,
  };
}

type SubmitMock = Mock<(fields: CredentialFields) => void | Promise<unknown>>;

function formEl(
  provider: CatalogProvider,
  status: ProviderStatus | null,
  onSubmit: SubmitMock,
  busy = false,
) {
  return (
    <ProviderCredentialForm
      provider={provider}
      status={status}
      busy={busy}
      onSubmit={onSubmit}
    />
  );
}

function renderForm(
  provider: CatalogProvider,
  status: ProviderStatus | null,
  opts: { busy?: boolean; onSubmit?: SubmitMock } = {},
) {
  const onSubmit = opts.onSubmit ?? vi.fn().mockResolvedValue(undefined);
  return { ...render(formEl(provider, status, onSubmit, opts.busy)), onSubmit };
}

const input = (label: string) => screen.getByLabelText(label) as HTMLInputElement;
const saveButton = () =>
  screen.getByRole("button", { name: "Save & test" }) as HTMLButtonElement;
const save = () => userEvent.click(saveButton());

const OLLAMA = catalogProvider("ollama", [
  field({ key: "base_url", label: "Base URL", kind: "url", required: true }),
]);

const ANTHROPIC = catalogProvider("anthropic", [
  field({ key: "api_key", label: "API Key", kind: "secret", required: true }),
  field({ key: "base_url", label: "Base URL", kind: "url" }),
]);

const BEDROCK = catalogProvider("bedrock", [
  field({ key: "region", label: "Region", kind: "text", required: true }),
  field({ key: "access_key_id", label: "Access Key ID", kind: "text", required: true }),
  field({ key: "secret_access_key", label: "Secret Key", kind: "secret", required: true }),
]);

const AZURE = catalogProvider("azure", [
  field({ key: "endpoint", label: "Endpoint", kind: "url", required: true }),
  field({ key: "deployment", label: "Deployment", kind: "text", required: false }),
  field({ key: "api_key", label: "API Key", kind: "secret", required: true }),
]);

const CONFIGURED = providerStatus({ configured: true, source: "workspace" });

const BEDROCK_STORED = providerStatus({
  provider: "bedrock",
  configured: true,
  source: "workspace",
  extra_config_public: { region: "us-east-1", access_key_id: "AKIA123" },
  extra_config_secret_keys: ["secret_access_key"],
});

async function fillBedrock() {
  await userEvent.type(input("Region"), "us-east-1");
  await userEvent.type(input("Access Key ID"), "AKIA123");
  await userEvent.type(input("Secret Key"), "shhh");
}

// --- schema-driven rendering -------------------------------------------------

test("a keyless provider renders exactly its declared field and no password input", () => {
  const { container } = renderForm(OLLAMA, null);
  expect(container.querySelectorAll("input")).toHaveLength(1);
  expect(container.querySelectorAll('input[type="password"]')).toHaveLength(0);
  expect(input("Base URL")).toBeTruthy();
});

test("a stored secret renders empty with the keep-it hint", () => {
  renderForm(ANTHROPIC, CONFIGURED);
  expect(input("API Key").type).toBe("password");
  expect(input("API Key").value).toBe("");
  expect(screen.getByText("configured — leave blank to keep")).toBeTruthy();
});

test("every control carries a visible label bound to it", () => {
  const { container } = renderForm(BEDROCK, null);
  const labels = Array.from(container.querySelectorAll("label"));
  expect(labels).toHaveLength(3);
  for (const label of labels) {
    expect(label.textContent?.trim()).toBeTruthy();
    expect(label.getAttribute("for")).toBeTruthy();
    // getByLabelText resolves the htmlFor/id pairing — no aria-label anywhere.
    expect(input(label.textContent as string)).toBeTruthy();
  }
  expect(container.querySelectorAll("input[aria-label]")).toHaveLength(0);
});

test("a secret opts out of password-manager capture; every input opts out of autocapitalize", () => {
  renderForm(BEDROCK, null);
  // autocomplete="off" is deliberately ignored by Chrome/Safari on password fields.
  expect(input("Secret Key").getAttribute("autocomplete")).toBe("new-password");
  expect(input("Region").getAttribute("autocomplete")).toBe("off");
  for (const label of ["Region", "Access Key ID", "Secret Key"]) {
    expect(input(label).getAttribute("autocapitalize")).toBe("none");
    expect(input(label).getAttribute("autocorrect")).toBe("off");
    expect(input(label).getAttribute("spellcheck")).toBe("false");
  }
});

test("the stored-secret hint is announced with its input", () => {
  renderForm(BEDROCK, BEDROCK_STORED);
  const hintId = input("Secret Key").getAttribute("aria-describedby");
  expect(hintId).toBeTruthy();
  expect(document.getElementById(hintId as string)?.textContent).toBe(
    "configured — leave blank to keep",
  );
  // A field with nothing stored has nothing to describe.
  expect(input("Region").getAttribute("aria-describedby")).toBeNull();
});

// --- what reaches the wire ---------------------------------------------------

test("a blank secret is omitted from the body while base_url still goes", async () => {
  const { onSubmit } = renderForm(
    ANTHROPIC,
    providerStatus({
      configured: true,
      source: "workspace",
      base_url: "https://api.anthropic.com",
    }),
  );
  await save();
  expect(onSubmit).toHaveBeenCalledTimes(1);
  expect(onSubmit.mock.calls[0][0]).toEqual({ base_url: "https://api.anthropic.com" });
});

test("a retyped secret is sent and cleared from state after a successful save", async () => {
  const { onSubmit } = renderForm(ANTHROPIC, CONFIGURED);
  await userEvent.type(input("API Key"), "sk-new-key");
  await save();

  expect(onSubmit.mock.calls[0][0]).toEqual({ api_key: "sk-new-key", base_url: null });
  await waitFor(() => expect(input("API Key").value).toBe(""));
});

test("fields other than api_key/base_url are collected into extra_config", async () => {
  const { onSubmit } = renderForm(BEDROCK, null);
  await fillBedrock();
  await save();
  expect(onSubmit.mock.calls[0][0]).toEqual({
    extra_config: {
      region: "us-east-1",
      access_key_id: "AKIA123",
      secret_access_key: "shhh",
    },
  });
});

test("editing a public extra field omits the stored extra secret from the body", async () => {
  // The server merges extra_config PER KEY, so an omitted key is retained. Sending
  // the map without `secret_access_key` is how the form keeps a secret it cannot read.
  const { onSubmit } = renderForm(BEDROCK, BEDROCK_STORED);
  await userEvent.clear(input("Region"));
  await userEvent.type(input("Region"), "eu-west-1");
  await save();

  expect(onSubmit.mock.calls[0][0]).toEqual({
    extra_config: { region: "eu-west-1", access_key_id: "AKIA123" },
  });
  expect(onSubmit.mock.calls[0][0].extra_config).not.toHaveProperty("secret_access_key");
});

test("blanking an optional pre-filled extra field clears it while a blank secret is kept", async () => {
  const { onSubmit } = renderForm(
    AZURE,
    providerStatus({
      provider: "azure",
      configured: true,
      source: "workspace",
      extra_config_public: { endpoint: "https://x.openai.azure.com", deployment: "gpt4o" },
    }),
  );
  await userEvent.clear(input("Deployment"));
  await save();

  // The secret is omitted (kept); the emptied public field is an explicit null.
  expect(onSubmit.mock.calls[0][0]).toEqual({
    extra_config: { endpoint: "https://x.openai.azure.com", deployment: null },
  });
});

test("a rejected submit preserves what was typed", async () => {
  const onSubmit = vi.fn().mockRejectedValue(new Error("nope"));
  renderForm(ANTHROPIC, CONFIGURED, { onSubmit });
  await userEvent.type(input("API Key"), "sk-typed");
  await save();

  await waitFor(() => expect(onSubmit).toHaveBeenCalled());
  expect(input("API Key").value).toBe("sk-typed");
});

// --- when Save is allowed ----------------------------------------------------

test("save is disabled while a required non-secret field is empty", async () => {
  renderForm(BEDROCK, null);
  expect(saveButton().disabled).toBe(true);
  await fillBedrock();
  expect(saveButton().disabled).toBe(false);
});

test("save is enabled when a required secret is blank but already stored", () => {
  renderForm(BEDROCK, BEDROCK_STORED);
  expect(saveButton().disabled).toBe(false);
  expect(input("Region").value).toBe("us-east-1");
  expect(input("Secret Key").value).toBe("");
});

test("busy disables the fields and the button", () => {
  renderForm(ANTHROPIC, null, { busy: true });
  expect(input("API Key").disabled).toBe(true);
  expect(saveButton().disabled).toBe(true);
});

test("submitting the form directly still honours the required-check", () => {
  // The disabled button is not the only route in: an implicit submit, or a race
  // against the busy state, reaches the handler regardless. Both must enforce the
  // same rule, so this fires submit at the form and bypasses the button entirely.
  const { container, onSubmit } = renderForm(BEDROCK, null);
  fireEvent.submit(container.querySelector("form") as HTMLFormElement);
  expect(onSubmit).not.toHaveBeenCalled();
});

test("submitting the form directly while busy is refused", () => {
  const { container, onSubmit } = renderForm(ANTHROPIC, CONFIGURED, { busy: true });
  fireEvent.submit(container.querySelector("form") as HTMLFormElement);
  expect(onSubmit).not.toHaveBeenCalled();
});

test("submitting the form directly succeeds once the required fields are filled", async () => {
  const { container, onSubmit } = renderForm(BEDROCK, null);
  await fillBedrock();
  fireEvent.submit(container.querySelector("form") as HTMLFormElement);
  expect(onSubmit).toHaveBeenCalledTimes(1);
});

// --- when the pre-fill is re-derived ----------------------------------------

test("a status arriving after mount is folded into the pre-fill", () => {
  // This form always sends the fields it declares, so a stale empty pre-fill is not
  // a no-op — it is a silent clear of a base URL the founder never touched.
  const { rerender } = renderForm(ANTHROPIC, null);
  expect(input("Base URL").value).toBe("");

  rerender(
    formEl(
      ANTHROPIC,
      providerStatus({
        configured: true,
        source: "workspace",
        base_url: "https://proxy.internal/v1",
      }),
      vi.fn(),
    ),
  );
  expect(input("Base URL").value).toBe("https://proxy.internal/v1");
});

test("a revoke re-derives the pre-fill, so a stale endpoint cannot be written back", () => {
  // The API emits a ProviderStatus for EVERY catalogued provider whether or not a
  // row exists, so a revoke is object -> object (`configured` flips, `base_url`
  // goes null), never object -> null. Keying on loaded-ness alone left the revoked
  // endpoint sitting in the input for the next Save to write straight back.
  const { rerender } = renderForm(
    ANTHROPIC,
    providerStatus({
      configured: true,
      source: "workspace",
      base_url: "https://proxy.internal/v1",
    }),
  );
  expect(input("Base URL").value).toBe("https://proxy.internal/v1");

  rerender(
    formEl(
      ANTHROPIC,
      providerStatus({ configured: false, source: "none", base_url: null }),
      vi.fn(),
    ),
  );
  expect(input("Base URL").value).toBe("");
  expect(screen.queryByText("configured — leave blank to keep")).toBeNull();
});

test("a post-save refetch does not wipe half-typed input", async () => {
  // The other half of the derive key: a status REPLACED by one of the same
  // configured-ness must not re-derive, or a background refresh eats what is being
  // typed. Only the null->status and configured->cleared transitions may.
  const configured = () =>
    providerStatus({ configured: true, source: "workspace", base_url: "https://a/v1" });
  const { rerender } = renderForm(ANTHROPIC, configured());
  await userEvent.type(input("API Key"), "sk-half-typed");

  rerender(formEl(ANTHROPIC, configured(), vi.fn()));
  expect(input("API Key").value).toBe("sk-half-typed");
});
