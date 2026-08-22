"use client";

import type { ReactNode } from "react";

import type { CatalogProvider, ProviderStatus } from "@/lib/types";

/** §9.3 chip — one height (20px), one radius. The fence there is on SIZES: do
 *  not add a fourth chip size. Variants are a different axis and are added as
 *  the states they name become distinguishable. */
const CHIP_BASE =
  "inline-flex items-center h-[20px] px-[8px] rounded-full text-[11px] " +
  "font-medium whitespace-nowrap shrink-0";

const CHIP_VARIANTS = {
  neutral: "bg-surface-3 text-t-tertiary",
  success: "bg-j-success-soft text-j-success",
  warning: "bg-j-warning-soft text-j-warning",
  error: "bg-j-error-soft text-j-error",
  info: "bg-j-primary-soft text-j-primary",
  outline: "bg-transparent text-t-muted border border-b-primary",
} as const;

type ChipVariant = keyof typeof CHIP_VARIANTS;

function Chip({
  variant = "neutral",
  children,
}: {
  variant?: ChipVariant;
  children: ReactNode;
}) {
  return <span className={`${CHIP_BASE} ${CHIP_VARIANTS[variant]}`}>{children}</span>;
}

/** `sm` ghost button, MINUS its text colour. 44px tall below the `sm`
 *  breakpoint so a row action stays a legal touch target on a phone.
 *
 *  The colour is deliberately not in here. Stacking `text-j-error` after
 *  `text-t-secondary` in the class attribute does NOT make it win: at equal
 *  specificity the cascade is decided by STYLESHEET order, and Tailwind emits
 *  colour utilities sorted by token — `.text-j-error` lands before
 *  `.text-t-secondary`, so grey would win and the destructive action would
 *  render identical to the benign ones. */
const GHOST_BTN_BASE =
  "inline-flex items-center justify-center h-[44px] sm:h-[30px] px-[11px] " +
  "text-[13px] font-medium rounded-[var(--radius-md)] bg-transparent " +
  "border border-b-primary hover:bg-surface-2 " +
  "disabled:opacity-45 cursor-pointer disabled:cursor-default";

const GHOST_BTN = `${GHOST_BTN_BASE} text-t-secondary`;

/** Ghost in the error colour AT REST — not on hover. A hover-only danger colour
 *  lives inside `@media (hover: hover)`, so on a phone the destructive action
 *  would never be coloured at all. Still ghost, not a filled danger button:
 *  revoking a key is a normal workspace action. */
const DANGER_GHOST_BTN = `${GHOST_BTN_BASE} text-j-error`;

const AUTH_KIND_LABELS: Record<CatalogProvider["auth_kind"], string> = {
  api_key: "API key",
  keyless_base_url: "Base URL",
  aws_sigv4: "AWS SigV4",
  azure_deployment: "Azure deployment",
};

/** Where the credential comes from. Said out loud because for `env` and
 *  `default` the missing Remove button would otherwise read as a bug rather
 *  than as "this one isn't this workspace's to delete". */
const SOURCE_DETAIL: Record<ProviderStatus["source"], string> = {
  workspace: "workspace key",
  env: "from environment",
  default: "deployment default",
  none: "",
};

/** How a configured provider's `status` reads. The three keys are the whole set
 *  the backend produces for a credential row: `untested` is the column default,
 *  `valid` and `invalid` are what a test writes back.
 *
 *  `invalid` is NOT amber. It means the provider REJECTED the credential, so
 *  every tier bound to it fails at runtime — rendering it in the same colour as
 *  the benign `untested` teaches the founder to read the second as the first. */
const STATUS_CHIPS: Record<string, { label: string; variant: ChipVariant }> = {
  valid: { label: "Connected", variant: "success" },
  invalid: { label: "Invalid credential", variant: "error" },
  untested: { label: "Untested", variant: "warning" },
};

/** An unrecognised status is neither trusted nor treated as a hard failure. */
function statusChipFor(status: string): { label: string; variant: ChipVariant } {
  return STATUS_CHIPS[status] ?? { label: status, variant: "warning" };
}

/** Three states, derived once, driving the dot. `degraded` is
 *  configured-but-not-valid: there IS a credential, it just did not answer. */
type Connection = "connected" | "degraded" | "disconnected";

function connectionOf(status: ProviderStatus): Connection {
  if (!status.configured) return "disconnected";
  return status.status === "valid" ? "connected" : "degraded";
}

const DOT_CLASSES: Record<Connection, string> = {
  connected: "bg-j-success",
  degraded: "bg-j-warning",
  disconnected: "border-[1.5px] border-t-muted bg-transparent",
};

function StatusDot({ connection }: { connection: Connection }) {
  return (
    <span
      aria-hidden="true"
      className={`w-[7px] h-[7px] rounded-full shrink-0 ${DOT_CLASSES[connection]}`}
    />
  );
}

/**
 * The 1px rule between two rows.
 *
 * A rule ELEMENT, not a border on the row, and rendered by the parent for two
 * reasons. It stays OUTSIDE the expanded row's `bg-j-primary/5` tint, where a
 * `border-b` would be tinted along with it; and the parent can interleave
 * something else — a group heading, a section break — between two rows without
 * the row having any say in it.
 */
export function ProviderRowSeparator() {
  return <div aria-hidden="true" className="h-px bg-b-secondary" />;
}

export interface ProviderRowProps {
  /** Server-side connection state. The authority for which actions exist. */
  status: ProviderStatus;
  /** Catalog entry, carrying the display name and the credential schema. Null
   *  or absent for a provider the catalog no longer lists. */
  catalog?: CatalogProvider | null;
  /** Controlled by the parent tab — this component owns no state. */
  expanded: boolean;
  /** Disables every action while a credential call is in flight. */
  busy?: boolean;
  /** Why this row was opened for the founder, e.g. "Needed by the Fast tier".
   *  Rendered as a chip in the header. */
  reason?: string;
  onToggle: () => void;
  onTest: () => void;
  onRemove: () => void;
  /** The expanded body — in practice `<ProviderCredentialForm>`.
   *
   *  Taken as children rather than constructed here so the row stays purely
   *  presentational: the form needs an `onSubmit` wired to the owning tab's
   *  credentials hook, and threading that through the row would put an
   *  API-shaped prop on a component that calls no API. */
  children?: ReactNode;
}

/**
 * One provider in the Providers tab: connection state on the left, the actions
 * that are actually meaningful for that state on the right.
 *
 * The action set is not cosmetic. `Remove` is withheld from an env-backed or
 * deployment-default credential because DELETE removes the workspace
 * credential row and nothing else — offering it there is a button that appears
 * to work and changes nothing.
 */
export function ProviderRow({
  status,
  catalog,
  expanded,
  busy = false,
  reason,
  onToggle,
  onTest,
  onRemove,
  children,
}: ProviderRowProps) {
  // A provider with no catalog entry has no display name and no credential
  // schema, so nothing but Remove is meaningful. Derived from BOTH facts: a
  // missing entry is treated exactly like `catalogued: false` rather than
  // offering a Connect that opens a form with no fields.
  const entry = status.catalogued ? (catalog ?? null) : null;
  const uncatalogued = entry === null;

  const connection = connectionOf(status);
  const name = entry?.display_name ?? status.provider;

  // Remove is gated on ownership to stop a workspace deleting an env or
  // deployment-default credential that is not its to delete. An UNCATALOGUED
  // row is not that case: it is a stray whose provider the catalog no longer
  // lists (key material that no longer decrypts reports
  // `configured=false, source="none"`), and it exists precisely so it can be
  // removed. Withholding Remove there strands it permanently.
  const canRemove = uncatalogued || status.source === "workspace";

  // Connected-but-inherited offers Override (add a workspace key that wins)
  // rather than Edit (change the one you already own).
  const primaryLabel = expanded
    ? "Cancel"
    : !status.configured
      ? "Connect"
      : status.source === "workspace"
        ? "Edit"
        : "Override";

  const chip = statusChipFor(status.status);

  const detail = status.configured
    ? SOURCE_DETAIL[status.source]
    : entry && entry.model_count > 0
      ? `${entry.model_count} models`
      : "";

  // An uncatalogued provider has no credential schema, so a form rendered for
  // it would have zero fields — and a zero-field form's required-field check is
  // vacuously satisfied, leaving a live Save button that 400s on the backend's
  // unknown-provider guard. Enforced HERE rather than contractually: the row
  // knows the provider is uncatalogued, and a parent that auto-expands a row
  // (see `reason`) never went through the toggle that would have hidden it.
  const showBody = expanded && !uncatalogued;
  const bodyId = `provider-row-body-${status.provider}`;

  return (
    <div
      className={`border-l-2 ${
        expanded ? "border-j-primary bg-j-primary/5" : "border-transparent"
      }`}
    >
      <div className="flex items-center gap-[13px] py-[11px] px-[20px]">
        <StatusDot connection={connection} />

        {/* `truncate` on a fixed width silently eats a long display name, and
            there is no other place it appears — so keep it reachable on hover. */}
        <span
          title={name}
          className="w-[150px] shrink-0 text-[14px] font-medium text-t-primary truncate"
        >
          {name}
        </span>

        {entry && <Chip>{AUTH_KIND_LABELS[entry.auth_kind]}</Chip>}
        {status.configured ? (
          <Chip variant={chip.variant}>{chip.label}</Chip>
        ) : (
          <Chip variant="outline">Not connected</Chip>
        )}
        {reason && <Chip variant="info">{reason}</Chip>}

        <span className="flex-1 min-w-0 text-[11.5px] text-t-muted truncate">
          {detail}
          {status.base_url && (
            <>
              {detail && " · "}
              <span className="font-mono">{status.base_url}</span>
            </>
          )}
        </span>

        {/* Every action names its provider. The assembled tab renders one row
            per provider, so a screen reader's button list would otherwise read
            "Remove, Remove, Remove" with no way to tell which key is about to
            be revoked. A group label on this wrapper is not a substitute —
            group labels are not reliably announced during list navigation. */}
        <div className="flex items-center gap-[7px] shrink-0">
          {!uncatalogued && status.configured && (
            <button
              type="button"
              disabled={busy}
              onClick={onTest}
              aria-label={`Test ${name}`}
              className={GHOST_BTN}
            >
              Test
            </button>
          )}
          {!uncatalogued && (
            <button
              type="button"
              disabled={busy}
              onClick={onToggle}
              aria-expanded={expanded}
              aria-controls={showBody ? bodyId : undefined}
              aria-label={`${primaryLabel} ${name}`}
              className={GHOST_BTN}
            >
              {primaryLabel}
            </button>
          )}
          {canRemove && (
            <button
              type="button"
              disabled={busy}
              onClick={onRemove}
              aria-label={`Remove ${name}`}
              className={DANGER_GHOST_BTN}
            >
              Remove
            </button>
          )}
        </div>
      </div>

      {showBody && <div id={bodyId}>{children}</div>}
    </div>
  );
}
