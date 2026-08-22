"use client";

import { useCallback, useState } from "react";

import type { CatalogProvider, ProviderStatus } from "@/lib/types";
import type { CredentialFields } from "../hooks/use-provider-credentials";
import {
  buildCredentialFields,
  hasMissingRequired,
  isSecretStored,
} from "./credential-body";

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

/** The value a non-secret field starts at. The server deliberately returns these
 *  so the form round-trips them instead of clearing what was not retyped. */
function storedValue(key: string, status: ProviderStatus | null): string {
  if (!status) return "";
  if (key === "base_url") return status.base_url ?? "";
  return status.extra_config_public[key] ?? "";
}

/** Secrets are ALWAYS empty here — a stored secret is never sent back to a
 *  client, so pre-filling one would be inventing a value. */
function initialValues(
  provider: CatalogProvider,
  status: ProviderStatus | null,
): Record<string, string> {
  return Object.fromEntries(
    provider.credential_fields.map((field) => [
      field.key,
      field.kind === "secret" ? "" : storedValue(field.key, status),
    ]),
  );
}

/** Identity of the state currently held. Beyond the provider, it folds in whether a
 *  `status` has arrived and whether that status is CONFIGURED, because both of those
 *  transitions invalidate the pre-fill:
 *
 *  * `null → status` — a status arriving after first mount. This form always sends
 *    the fields it declares, so a stale empty pre-fill is not a no-op, it is a silent
 *    clear of a value the founder never touched.
 *  * `configured → cleared` — a revoke. The API emits a ProviderStatus for EVERY
 *    catalogued provider whether or not a row exists, so a revoke is `object → object`
 *    (`configured` flips, `base_url` goes null), never `object → null`. Without
 *    `configured` in the key the inputs would keep showing the revoked endpoint and
 *    the next Save would write it straight back.
 *
 *  A status merely REPLACED by a later one of the same configured-ness (the refetch
 *  after a save) still does not re-derive, which is what stops a background refresh
 *  wiping half-typed input. */
function deriveKey(provider: CatalogProvider, status: ProviderStatus | null): string {
  const phase = status ? (status.configured ? "configured" : "clear") : "empty";
  return `${provider.provider}:${phase}`;
}

export interface ProviderCredentialFormProps {
  /** Carries the credential SCHEMA. The field set is read from here and never
   *  hard-coded: a provider that declares no `api_key` renders no key input. */
  provider: CatalogProvider;
  /** Current server-side state — `null` for a provider with no credential. Required
   *  rather than optional so a caller cannot forget it and silently get the
   *  unconfigured rendering for a configured provider. */
  status: ProviderStatus | null;
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
  // Keyed so switching providers — or a status arriving late — re-derives rather
  // than leaking one state's values into another's form. Adjusted during render
  // (React's supported derive-on-prop-change pattern), never from an effect.
  const [state, setState] = useState(() => ({
    key: deriveKey(provider, status),
    values: initialValues(provider, status),
  }));
  const key = deriveKey(provider, status);
  if (state.key !== key) {
    // Re-renders immediately with the new state; this pass's output is discarded.
    setState({ key, values: initialValues(provider, status) });
  }
  const values = state.values;

  const setValue = useCallback((fieldKey: string, value: string) => {
    setState((prev) => ({ ...prev, values: { ...prev.values, [fieldKey]: value } }));
  }, []);

  const fields = provider.credential_fields;
  const missingRequired = hasMissingRequired(fields, values, status);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      // The disabled button and this guard state the same rule; both enforce it,
      // so an implicit Enter-submit can never outrun the button's disabled state.
      if (busy || hasMissingRequired(fields, values, status)) return;
      try {
        await onSubmit(buildCredentialFields(fields, values));
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
          Object.entries(prev.values).map(([k, value]) => {
            const spec = fields.find((f) => f.key === k);
            return [k, spec?.kind === "secret" ? "" : value];
          }),
        ),
      }));
    },
    [busy, fields, onSubmit, status, values],
  );

  return (
    // noValidate pre-empts browser constraint validation. No input carries a
    // constraint attribute today (no `required`, and `inputMode="url"` is not
    // `type="url"`), so nothing is being suppressed yet — it is here so that adding
    // one later cannot produce a validation bubble contradicting a Save button
    // already disabled for the same reason. This form owns its own required-check.
    <form noValidate onSubmit={handleSubmit} className="px-[20px] pb-[15px]">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-[12px] mb-[11px]">
        {fields.map((field) => {
          const id = `cred-${provider.provider}-${field.key}`;
          const isSecret = field.kind === "secret";
          const stored = isSecret && isSecretStored(field.key, status);
          return (
            <div key={field.key}>
              <label htmlFor={id} className={LABEL_CLASS}>
                {field.label}
              </label>
              <input
                id={id}
                type={isSecret ? "password" : "text"}
                inputMode={field.kind === "url" ? "url" : undefined}
                // Chrome and Safari deliberately IGNORE autocomplete="off" on a
                // password field and still offer to save it — which would put the
                // founder's key in a synced browser store, outside the
                // encrypted-at-rest guarantee this form's own footer makes.
                autoComplete={isSecret ? "new-password" : "off"}
                // iOS Safari capitalises the first character of a text input:
                // `us-east-1` becomes `Us-east-1` and an access key ID is corrupted
                // into an opaque provider auth error rather than a form error.
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                disabled={busy}
                placeholder={field.placeholder ?? undefined}
                aria-describedby={stored ? `${id}-hint` : undefined}
                value={values[field.key] ?? ""}
                onChange={(e) => setValue(field.key, e.target.value)}
                className={CTL_CLASS}
              />
              {stored && (
                <p id={`${id}-hint`} className={`${HINT_CLASS} mt-[5px]`}>
                  {STORED_SECRET_HINT}
                </p>
              )}
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
