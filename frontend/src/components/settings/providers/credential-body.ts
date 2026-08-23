import type {
  CredentialFields,
  CredentialFieldSpec,
  ProviderStatus,
} from "@/lib/types";

/**
 * The wire format of a credential save, and the rules that decide it.
 *
 * Split out of the form because this is the security-relevant half on the client:
 * it decides which blanks mean "keep the secret I cannot read back" and which mean
 * "clear this". Getting that backwards either destroys a stored key or silently
 * refuses to clear a field — neither of which a rendering test would notice — so it
 * lives in a small unit that can be reviewed and driven against a schema directly.
 */

/** Whether the server already holds a value for this SECRET field. `api_key` is
 *  reported by `configured` (it is the credential itself); every other secret is
 *  listed by key — never by value — in `extra_config_secret_keys`. */
export function isSecretStored(key: string, status: ProviderStatus | null): boolean {
  if (!status) return false;
  return (
    status.extra_config_secret_keys.includes(key) ||
    (key === "api_key" && status.configured)
  );
}

/**
 * Fold the typed values into the request body. Exactly two field keys are
 * top-level — `api_key` and `base_url`; EVERY other declared field is a member
 * of `extra_config`.
 *
 * Blank means two different things, and the split is by `kind`, because the
 * server merges `extra_config` per key (omitted keeps, explicit null deletes):
 *   * a blank SECRET is omitted — it was rendered empty because its value can
 *     never be read back, so blank is "keep the stored one", not "clear it";
 *   * a blank non-secret is an explicit `null` — it was PRE-FILLED, so the
 *     founder emptying it is a deliberate clear. Omitting it instead would make
 *     the field unclearable. This is exactly what `base_url` already does.
 *
 * Values are trimmed. No provider's key, region, endpoint or deployment name may
 * carry surrounding whitespace, and trimming kills the commonest paste failure
 * (a trailing newline off a terminal or a docs page) before it becomes an opaque
 * provider auth error.
 */
export function buildCredentialFields(
  fields: readonly CredentialFieldSpec[],
  values: Record<string, string>,
): CredentialFields {
  let apiKey: string | undefined;
  let baseUrl: string | null | undefined;
  const extra: Record<string, unknown> = {};

  for (const field of fields) {
    const value = (values[field.key] ?? "").trim();
    if (field.key === "api_key") {
      if (value) apiKey = value;
    } else if (field.key === "base_url") {
      baseUrl = value || null;
    } else if (field.kind === "secret") {
      if (value) extra[field.key] = value;
    } else {
      extra[field.key] = value || null;
    }
  }

  return {
    ...(apiKey === undefined ? {} : { api_key: apiKey }),
    ...(baseUrl === undefined ? {} : { base_url: baseUrl }),
    ...(Object.keys(extra).length > 0 ? { extra_config: extra } : {}),
  };
}

/** A required secret that is already stored may legitimately be blank — blank
 *  means "keep the stored one". Every other required field must carry a value. */
export function hasMissingRequired(
  fields: readonly CredentialFieldSpec[],
  values: Record<string, string>,
  status: ProviderStatus | null,
): boolean {
  return fields.some(
    (field) =>
      field.required &&
      !(values[field.key] ?? "").trim() &&
      !(field.kind === "secret" && isSecretStored(field.key, status)),
  );
}
