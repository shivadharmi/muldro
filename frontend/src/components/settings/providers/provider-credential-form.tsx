"use client";

import { useCallback, useState } from "react";

import type { CatalogProvider, CredentialFieldSpec, ProviderStatus } from "@/lib/types";
import type { CredentialFields } from "../hooks/use-provider-credentials";

const CTL_CLASS =
  "w-full h-[44px] sm:h-[36px] text-[15px] sm:text-[14px] px-[12px] sm:px-[10px] " +
  "rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary text-t-primary " +
  "disabled:opacity-45";

const LABEL_CLASS =
  "block text-[10px] font-medium uppercase text-t-muted tracking-[.07em] " +
  "mb-[6px] sm:mb-[5px]";

const HINT_CLASS = "text-[11.5px] text-t-muted";

const PRIMARY_BTN_CLASS =
  "h-[44px] sm:h-[32px] px-[18px] sm:px-[13px] text-[13px] font-medium " +
  "rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg hover:bg-j-primary-hover " +
  "disabled:opacity-45 cursor-pointer disabled:cursor-default";

/** The hint shown under a secret whose value the server already holds. The value
 *  is never returned, so the input stays empty and blank means "keep it". */
const STORED_SECRET_HINT = "configured — leave blank to keep";

function LockIcon() {
  return (
    <svg
      viewBox="0 0 14 14"
      width={12}
      height={12}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      <rect x="2.5" y="6" width="9" height="6" rx="1.5" />
      <path d="M4.75 6V4.25a2.25 2.25 0 014.5 0V6" />
    </svg>
  );
}

/** Whether the server already holds a value for this SECRET field. `api_key` is
 *  reported by `configured` (it is the credential itself); every other secret is
 *  listed by key in `extra_config_secret_keys`. */
function isSecretStored(key: string, status: ProviderStatus | null | undefined): boolean {
  if (!status) return false;
  return (
    status.extra_config_secret_keys.includes(key) ||
    (key === "api_key" && status.configured)
  );
}

/** The value a non-secret field starts at. The server deliberately returns these
 *  so the form round-trips them instead of clearing what was not retyped. */
function storedValue(key: string, status: ProviderStatus | null | undefined): string {
  if (!status) return "";
  if (key === "base_url") return status.base_url ?? "";
  return status.extra_config_public[key] ?? "";
}

/** Secrets are ALWAYS empty here — a stored secret is never sent back to a
 *  client, so pre-filling one would be inventing a value. */
function initialValues(
  provider: CatalogProvider,
  status: ProviderStatus | null | undefined,
): Record<string, string> {
  return Object.fromEntries(
    provider.credential_fields.map((field) => [
      field.key,
      field.kind === "secret" ? "" : storedValue(field.key, status),
    ]),
  );
}

/**
 * Fold the typed values into the request body. Exactly two field keys are
 * top-level — `api_key` and `base_url`; EVERY other declared field is a member
 * of `extra_config`. A blank secret is omitted entirely rather than sent as `""`
 * or `null`, which is what makes "leave blank to keep" true at the wire level.
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
    } else if (value) {
      extra[field.key] = value;
    }
  }

  return {
    ...(apiKey === undefined ? {} : { api_key: apiKey }),
    ...(baseUrl === undefined ? {} : { base_url: baseUrl }),
    ...(Object.keys(extra).length > 0 ? { extra_config: extra } : {}),
  };
}

export interface ProviderCredentialFormProps {
  /** Carries the credential SCHEMA. The field set is read from here and never
   *  hard-coded: a provider that declares no `api_key` renders no key input. */
  provider: CatalogProvider;
  /** Current server-side state, or null/undefined for an unconfigured provider. */
  status?: ProviderStatus | null;
  busy: boolean;
  /** Submits the body. This component never calls the API itself — the owning
   *  tab holds the credentials hook. A rejection leaves the typed values alone
   *  so the founder can retry without re-typing a long key. */
  onSubmit: (fields: CredentialFields) => void | Promise<unknown>;
}

/**
 * The schema-driven credential form for one provider.
 *
 * There is no fixed `(api_key, base_url)` pair here. Bedrock wants a region and
 * a key pair, Azure an endpoint plus a deployment, Ollama a base URL and no
 * secret at all — so the inputs are generated from
 * `CatalogProvider.credential_fields`, in declaration order.
 */
export function ProviderCredentialForm({
  provider,
  status,
  busy,
  onSubmit,
}: ProviderCredentialFormProps) {
  // Keyed by provider so switching providers re-derives rather than leaking one
  // provider's typed values into another's form. Adjusted during render (React's
  // supported derive-on-prop-change pattern), never from an effect.
  const [state, setState] = useState(() => ({
    key: provider.provider,
    values: initialValues(provider, status),
  }));
  if (state.key !== provider.provider) {
    // Re-renders immediately with the new state; this pass's output is discarded.
    setState({ key: provider.provider, values: initialValues(provider, status) });
  }
  const values = state.values;

  const setValue = useCallback((key: string, value: string) => {
    setState((prev) => ({ ...prev, values: { ...prev.values, [key]: value } }));
  }, []);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (busy) return;
      try {
        await onSubmit(buildCredentialFields(provider.credential_fields, values));
      } catch {
        // REPORTING a failure is the caller's job — it owns the toast and the
        // error text. All this form needs from a rejection is "do not clear
        // what was typed", so the founder can retry without re-typing the key.
        return;
      }
      // Only on success: a secret must not be left sitting in component state
      // once the server holds it.
      setState((prev) => ({
        ...prev,
        values: Object.fromEntries(
          Object.entries(prev.values).map(([key, value]) => {
            const spec = provider.credential_fields.find((f) => f.key === key);
            return [key, spec?.kind === "secret" ? "" : value];
          }),
        ),
      }));
    },
    [busy, onSubmit, provider.credential_fields, values],
  );

  // A required secret that is already stored may legitimately be blank — blank
  // means "keep the stored one". Every other required field must carry a value.
  const missingRequired = provider.credential_fields.some(
    (field) =>
      field.required &&
      !(values[field.key] ?? "").trim() &&
      !(field.kind === "secret" && isSecretStored(field.key, status)),
  );

  return (
    <form noValidate onSubmit={handleSubmit} className="px-[20px] pb-[15px]">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-[12px] mb-[11px]">
        {provider.credential_fields.map((field) => {
          const id = `cred-${provider.provider}-${field.key}`;
          const stored = field.kind === "secret" && isSecretStored(field.key, status);
          return (
            <div key={field.key}>
              <label htmlFor={id} className={LABEL_CLASS}>
                {field.label}
              </label>
              <input
                id={id}
                type={field.kind === "secret" ? "password" : "text"}
                inputMode={field.kind === "url" ? "url" : undefined}
                autoComplete="off"
                disabled={busy}
                placeholder={field.placeholder ?? undefined}
                value={values[field.key] ?? ""}
                onChange={(e) => setValue(field.key, e.target.value)}
                className={CTL_CLASS}
              />
              {stored && <p className={`${HINT_CLASS} mt-[5px]`}>{STORED_SECRET_HINT}</p>}
            </div>
          );
        })}
      </div>

      <div className="flex items-center justify-between gap-3">
        <p className={`${HINT_CLASS} flex items-center gap-1.5`}>
          <LockIcon />
          Encrypted at rest. Never shown again after saving.
        </p>
        <button
          type="submit"
          disabled={busy || missingRequired}
          className={PRIMARY_BTN_CLASS}
        >
          Save &amp; test
        </button>
      </div>
    </form>
  );
}
