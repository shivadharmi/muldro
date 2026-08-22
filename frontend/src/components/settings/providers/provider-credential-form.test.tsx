import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";

import {
  ProviderCredentialForm,
  buildCredentialFields,
} from "./provider-credential-form";
import type { CatalogProvider, CredentialFieldSpec, ProviderStatus } from "@/lib/types";

function field(over: Partial<CredentialFieldSpec> & { key: string }): CredentialFieldSpec {
  return {
    label: over.key,
    kind: "text",
    required: false,
    placeholder: null,
    ...over,
  };
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

test("a keyless provider renders exactly its declared field and no password input", () => {
  const { container } = render(
    <ProviderCredentialForm provider={OLLAMA} status={null} busy={false} onSubmit={vi.fn()} />,
  );
  expect(container.querySelectorAll("input")).toHaveLength(1);
  expect(container.querySelectorAll('input[type="password"]')).toHaveLength(0);
  expect(screen.getByLabelText("Base URL")).toBeTruthy();
});

test("a stored secret renders empty with the keep-it hint", () => {
  render(
    <ProviderCredentialForm
      provider={ANTHROPIC}
      status={providerStatus({ configured: true, source: "workspace" })}
      busy={false}
      onSubmit={vi.fn()}
    />,
  );
  const input = screen.getByLabelText("API Key") as HTMLInputElement;
  expect(input.type).toBe("password");
  expect(input.value).toBe("");
  expect(screen.getByText("configured — leave blank to keep")).toBeTruthy();
});

test("a blank secret is omitted from the body while base_url still goes", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(
    <ProviderCredentialForm
      provider={ANTHROPIC}
      status={providerStatus({
        configured: true,
        source: "workspace",
        base_url: "https://api.anthropic.com",
      })}
      busy={false}
      onSubmit={onSubmit}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Save & test" }));
  expect(onSubmit).toHaveBeenCalledTimes(1);
  expect(onSubmit.mock.calls[0][0]).toEqual({ base_url: "https://api.anthropic.com" });
});

test("a retyped secret is sent and cleared from state after a successful save", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(
    <ProviderCredentialForm
      provider={ANTHROPIC}
      status={providerStatus({ configured: true, source: "workspace" })}
      busy={false}
      onSubmit={onSubmit}
    />,
  );
  const input = screen.getByLabelText("API Key") as HTMLInputElement;
  await userEvent.type(input, "sk-new-key");
  await userEvent.click(screen.getByRole("button", { name: "Save & test" }));

  expect(onSubmit.mock.calls[0][0]).toEqual({ api_key: "sk-new-key", base_url: null });
  await waitFor(() =>
    expect((screen.getByLabelText("API Key") as HTMLInputElement).value).toBe(""),
  );
});

test("fields other than api_key/base_url are collected into extra_config", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(
    <ProviderCredentialForm provider={BEDROCK} status={null} busy={false} onSubmit={onSubmit} />,
  );
  await userEvent.type(screen.getByLabelText("Region"), "us-east-1");
  await userEvent.type(screen.getByLabelText("Access Key ID"), "AKIA123");
  await userEvent.type(screen.getByLabelText("Secret Key"), "shhh");
  await userEvent.click(screen.getByRole("button", { name: "Save & test" }));

  expect(onSubmit.mock.calls[0][0]).toEqual({
    extra_config: {
      region: "us-east-1",
      access_key_id: "AKIA123",
      secret_access_key: "shhh",
    },
  });
});

test("save is disabled while a required non-secret field is empty", async () => {
  render(
    <ProviderCredentialForm provider={BEDROCK} status={null} busy={false} onSubmit={vi.fn()} />,
  );
  const button = screen.getByRole("button", { name: "Save & test" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);

  await userEvent.type(screen.getByLabelText("Region"), "us-east-1");
  await userEvent.type(screen.getByLabelText("Access Key ID"), "AKIA123");
  await userEvent.type(screen.getByLabelText("Secret Key"), "shhh");
  expect(button.disabled).toBe(false);
});

test("save is enabled when a required secret is blank but already stored", () => {
  render(
    <ProviderCredentialForm
      provider={BEDROCK}
      status={providerStatus({
        provider: "bedrock",
        configured: true,
        source: "workspace",
        extra_config_public: { region: "us-east-1", access_key_id: "AKIA123" },
        extra_config_secret_keys: ["secret_access_key"],
      })}
      busy={false}
      onSubmit={vi.fn()}
    />,
  );
  const button = screen.getByRole("button", { name: "Save & test" }) as HTMLButtonElement;
  expect(button.disabled).toBe(false);
  expect((screen.getByLabelText("Region") as HTMLInputElement).value).toBe("us-east-1");
  expect((screen.getByLabelText("Secret Key") as HTMLInputElement).value).toBe("");
});

test("every control carries a visible label bound to it", () => {
  const { container } = render(
    <ProviderCredentialForm provider={BEDROCK} status={null} busy={false} onSubmit={vi.fn()} />,
  );
  const labels = Array.from(container.querySelectorAll("label"));
  expect(labels).toHaveLength(3);
  for (const label of labels) {
    expect(label.textContent?.trim()).toBeTruthy();
    expect(label.getAttribute("for")).toBeTruthy();
    // getByLabelText resolves the htmlFor/id pairing — no aria-label anywhere.
    expect(screen.getByLabelText(label.textContent as string)).toBeTruthy();
  }
  expect(container.querySelectorAll("input[aria-label]")).toHaveLength(0);
});

test("busy disables the fields and the button", () => {
  render(
    <ProviderCredentialForm provider={ANTHROPIC} status={null} busy onSubmit={vi.fn()} />,
  );
  expect((screen.getByLabelText("API Key") as HTMLInputElement).disabled).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Save & test" }) as HTMLButtonElement).disabled,
  ).toBe(true);
});

const BEDROCK_STORED = providerStatus({
  provider: "bedrock",
  configured: true,
  source: "workspace",
  extra_config_public: { region: "us-east-1", access_key_id: "AKIA123" },
  extra_config_secret_keys: ["secret_access_key"],
});

test("editing a public extra field omits the stored extra secret from the body", async () => {
  // The server merges extra_config PER KEY, so an omitted key is retained. Sending
  // the map without `secret_access_key` is how the form keeps a secret it cannot read.
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(
    <ProviderCredentialForm
      provider={BEDROCK}
      status={BEDROCK_STORED}
      busy={false}
      onSubmit={onSubmit}
    />,
  );
  const region = screen.getByLabelText("Region") as HTMLInputElement;
  await userEvent.clear(region);
  await userEvent.type(region, "eu-west-1");
  await userEvent.click(screen.getByRole("button", { name: "Save & test" }));

  expect(onSubmit.mock.calls[0][0]).toEqual({
    extra_config: { region: "eu-west-1", access_key_id: "AKIA123" },
  });
  expect(onSubmit.mock.calls[0][0].extra_config).not.toHaveProperty("secret_access_key");
});

test("a rejected submit preserves what was typed", async () => {
  const onSubmit = vi.fn().mockRejectedValue(new Error("nope"));
  render(
    <ProviderCredentialForm
      provider={ANTHROPIC}
      status={providerStatus({ configured: true, source: "workspace" })}
      busy={false}
      onSubmit={onSubmit}
    />,
  );
  await userEvent.type(screen.getByLabelText("API Key"), "sk-typed");
  await userEvent.click(screen.getByRole("button", { name: "Save & test" }));

  await waitFor(() => expect(onSubmit).toHaveBeenCalled());
  expect((screen.getByLabelText("API Key") as HTMLInputElement).value).toBe("sk-typed");
});

test("a status arriving after mount is folded into the pre-fill", () => {
  // This form always sends the fields it declares, so a stale empty pre-fill is not
  // a no-op — it is a silent clear of a base URL the founder never touched.
  const { rerender } = render(
    <ProviderCredentialForm provider={ANTHROPIC} status={null} busy={false} onSubmit={vi.fn()} />,
  );
  expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe("");

  rerender(
    <ProviderCredentialForm
      provider={ANTHROPIC}
      status={providerStatus({
        configured: true,
        source: "workspace",
        base_url: "https://proxy.internal/v1",
      })}
      busy={false}
      onSubmit={vi.fn()}
    />,
  );
  expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe(
    "https://proxy.internal/v1",
  );
});

test("a secret opts out of password-manager capture; every input opts out of autocapitalize", () => {
  render(
    <ProviderCredentialForm provider={BEDROCK} status={null} busy={false} onSubmit={vi.fn()} />,
  );
  // autocomplete="off" is deliberately ignored by Chrome/Safari on password fields.
  expect(screen.getByLabelText("Secret Key").getAttribute("autocomplete")).toBe(
    "new-password",
  );
  expect(screen.getByLabelText("Region").getAttribute("autocomplete")).toBe("off");
  for (const label of ["Region", "Access Key ID", "Secret Key"]) {
    const input = screen.getByLabelText(label);
    expect(input.getAttribute("autocapitalize")).toBe("none");
    expect(input.getAttribute("autocorrect")).toBe("off");
    expect(input.getAttribute("spellcheck")).toBe("false");
  }
});

test("the stored-secret hint is announced with its input", () => {
  render(
    <ProviderCredentialForm
      provider={BEDROCK}
      status={BEDROCK_STORED}
      busy={false}
      onSubmit={vi.fn()}
    />,
  );
  const input = screen.getByLabelText("Secret Key");
  const hintId = input.getAttribute("aria-describedby");
  expect(hintId).toBeTruthy();
  expect(document.getElementById(hintId as string)?.textContent).toBe(
    "configured — leave blank to keep",
  );
  // A field with nothing stored has nothing to describe.
  expect(screen.getByLabelText("Region").getAttribute("aria-describedby")).toBeNull();
});

// --- the pure body-builder, driven directly against a schema ------------------

test("buildCredentialFields splits top-level keys from extra_config", () => {
  expect(
    buildCredentialFields(ANTHROPIC.credential_fields, {
      api_key: "sk-1",
      base_url: "https://x/v1",
    }),
  ).toEqual({ api_key: "sk-1", base_url: "https://x/v1" });

  expect(
    buildCredentialFields(BEDROCK.credential_fields, {
      region: "us-east-1",
      access_key_id: "AKIA1",
      secret_access_key: "",
    }),
  ).toEqual({ extra_config: { region: "us-east-1", access_key_id: "AKIA1" } });
});

test("buildCredentialFields omits a blank secret and nulls a blank non-secret", () => {
  // The server merges extra_config per key, so the two blanks must differ: a
  // secret was rendered empty (blank = keep), a non-secret was PRE-FILLED
  // (blank = the founder cleared it). Omitting the latter left it unclearable.
  expect(
    buildCredentialFields(ANTHROPIC.credential_fields, { api_key: "", base_url: "" }),
  ).toEqual({ base_url: null });
  expect(
    buildCredentialFields(BEDROCK.credential_fields, {
      region: "",
      access_key_id: "",
      secret_access_key: "",
    }),
  ).toEqual({ extra_config: { region: null, access_key_id: null } });
});

test("blanking an optional pre-filled extra field clears it while a blank secret is kept", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(
    <ProviderCredentialForm
      provider={AZURE}
      status={providerStatus({
        provider: "azure",
        configured: true,
        source: "workspace",
        extra_config_public: { endpoint: "https://x.openai.azure.com", deployment: "gpt4o" },
      })}
      busy={false}
      onSubmit={onSubmit}
    />,
  );
  await userEvent.clear(screen.getByLabelText("Deployment"));
  await userEvent.click(screen.getByRole("button", { name: "Save & test" }));

  // The secret is omitted (kept); the emptied public field is an explicit null.
  expect(onSubmit.mock.calls[0][0]).toEqual({
    extra_config: { endpoint: "https://x.openai.azure.com", deployment: null },
  });
});

test("buildCredentialFields trims values", () => {
  // A key pasted off a terminal or a docs page carries a trailing newline, which
  // otherwise surfaces as an opaque provider auth error rather than a form error.
  expect(
    buildCredentialFields(ANTHROPIC.credential_fields, {
      api_key: "  sk-1\n",
      base_url: " https://x/v1 ",
    }),
  ).toEqual({ api_key: "sk-1", base_url: "https://x/v1" });
  // Whitespace alone is blank, not a value.
  expect(
    buildCredentialFields(ANTHROPIC.credential_fields, { api_key: "   ", base_url: "" }),
  ).toEqual({ base_url: null });
});
