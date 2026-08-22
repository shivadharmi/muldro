import { test, expect } from "vitest";

import {
  buildCredentialFields,
  hasMissingRequired,
  isSecretStored,
} from "./credential-body";
import type { CredentialFieldSpec, ProviderStatus } from "@/lib/types";

function field(over: Partial<CredentialFieldSpec> & { key: string }): CredentialFieldSpec {
  return {
    label: over.key,
    kind: "text",
    required: false,
    placeholder: null,
    ...over,
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

const ANTHROPIC_FIELDS: CredentialFieldSpec[] = [
  field({ key: "api_key", label: "API Key", kind: "secret", required: true }),
  field({ key: "base_url", label: "Base URL", kind: "url" }),
];

const BEDROCK_FIELDS: CredentialFieldSpec[] = [
  field({ key: "region", label: "Region", kind: "text", required: true }),
  field({ key: "access_key_id", label: "Access Key ID", kind: "text", required: true }),
  field({ key: "secret_access_key", label: "Secret Key", kind: "secret", required: true }),
];

// --- isSecretStored ----------------------------------------------------------

test("a stored secret is reported by key name, never by value", () => {
  const status = providerStatus({
    configured: true,
    extra_config_secret_keys: ["secret_access_key"],
  });
  // api_key is the credential itself, so `configured` is what reports it.
  expect(isSecretStored("api_key", status)).toBe(true);
  expect(isSecretStored("secret_access_key", status)).toBe(true);
  expect(isSecretStored("region", status)).toBe(false);
  // An unconfigured provider holds nothing, whatever else the envelope says.
  expect(isSecretStored("api_key", providerStatus())).toBe(false);
  expect(isSecretStored("api_key", null)).toBe(false);
});

// --- buildCredentialFields ---------------------------------------------------

test("buildCredentialFields splits top-level keys from extra_config", () => {
  expect(
    buildCredentialFields(ANTHROPIC_FIELDS, {
      api_key: "sk-1",
      base_url: "https://x/v1",
    }),
  ).toEqual({ api_key: "sk-1", base_url: "https://x/v1" });

  expect(
    buildCredentialFields(BEDROCK_FIELDS, {
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
  expect(buildCredentialFields(ANTHROPIC_FIELDS, { api_key: "", base_url: "" })).toEqual({
    base_url: null,
  });
  expect(
    buildCredentialFields(BEDROCK_FIELDS, {
      region: "",
      access_key_id: "",
      secret_access_key: "",
    }),
  ).toEqual({ extra_config: { region: null, access_key_id: null } });
});

test("buildCredentialFields never sends a blank secret as empty string or null", () => {
  const body = buildCredentialFields(BEDROCK_FIELDS, {
    region: "us-east-1",
    access_key_id: "AKIA1",
    secret_access_key: "   ",
  });
  expect(body).not.toHaveProperty("api_key");
  expect(Object.keys(body.extra_config ?? {})).not.toContain("secret_access_key");
});

test("buildCredentialFields trims values", () => {
  // A key pasted off a terminal or a docs page carries a trailing newline, which
  // otherwise surfaces as an opaque provider auth error rather than a form error.
  expect(
    buildCredentialFields(ANTHROPIC_FIELDS, {
      api_key: "  sk-1\n",
      base_url: " https://x/v1 ",
    }),
  ).toEqual({ api_key: "sk-1", base_url: "https://x/v1" });
  // Whitespace alone is blank, not a value.
  expect(
    buildCredentialFields(ANTHROPIC_FIELDS, { api_key: "   ", base_url: "" }),
  ).toEqual({ base_url: null });
});

test("buildCredentialFields ignores values with no declared field", () => {
  // The schema is the authority on what is sent; leftover state cannot smuggle a
  // key the provider never declared (the server rejects those outright).
  expect(
    buildCredentialFields(ANTHROPIC_FIELDS, { api_key: "sk-1", base_url: "", zzz: "junk" }),
  ).toEqual({ api_key: "sk-1", base_url: null });
});

// --- hasMissingRequired ------------------------------------------------------

test("hasMissingRequired blocks on an empty required field", () => {
  expect(hasMissingRequired(BEDROCK_FIELDS, {}, null)).toBe(true);
  expect(
    hasMissingRequired(
      BEDROCK_FIELDS,
      { region: "us-east-1", access_key_id: "AKIA1", secret_access_key: "shhh" },
      null,
    ),
  ).toBe(false);
  // Whitespace is not a value.
  expect(
    hasMissingRequired(
      BEDROCK_FIELDS,
      { region: "  ", access_key_id: "AKIA1", secret_access_key: "shhh" },
      null,
    ),
  ).toBe(true);
});

test("hasMissingRequired allows a blank required secret that is already stored", () => {
  const stored = providerStatus({
    provider: "bedrock",
    configured: true,
    extra_config_secret_keys: ["secret_access_key"],
  });
  expect(
    hasMissingRequired(
      BEDROCK_FIELDS,
      { region: "us-east-1", access_key_id: "AKIA1", secret_access_key: "" },
      stored,
    ),
  ).toBe(false);
  // A blank required NON-secret is still missing, stored or not — it was pre-filled,
  // so blank means the founder cleared it.
  expect(
    hasMissingRequired(
      BEDROCK_FIELDS,
      { region: "", access_key_id: "AKIA1", secret_access_key: "" },
      stored,
    ),
  ).toBe(true);
});

test("an optional field is never missing", () => {
  expect(hasMissingRequired(ANTHROPIC_FIELDS, { api_key: "sk-1" }, null)).toBe(false);
});
