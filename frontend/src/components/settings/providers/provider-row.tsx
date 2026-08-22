"use client";

import type { ReactNode } from "react";

import type { CatalogProvider, ProviderStatus } from "@/lib/types";

/** §9.3 chip — one height (20px), one radius, five variants. Do not add a
 *  fourth SIZE: the chips align down a fixed-width column and a taller one
 *  breaks that alignment. */
const CHIP_BASE =
  "inline-flex items-center h-[20px] px-[8px] rounded-full text-[11px] " +
  "font-medium whitespace-nowrap shrink-0";

const CHIP_VARIANTS = {
  neutral: "bg-surface-3 text-t-tertiary",
  success: "bg-j-success-soft text-j-success",
  warning: "bg-j-warning-soft text-j-warning",
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

/** `sm` ghost button. 44px tall below the `sm` breakpoint so a row action stays
 *  a legal touch target on a phone. */
const GHOST_BTN =
  "inline-flex items-center justify-center h-[44px] sm:h-[30px] px-[11px] " +
  "text-[13px] font-medium rounded-[var(--radius-md)] bg-transparent " +
  "text-t-secondary border border-b-primary hover:bg-surface-2 " +
  "disabled:opacity-45 cursor-pointer disabled:cursor-default";

/** Ghost + error text. Never a filled danger button: revoking a key is a normal
 *  workspace action, not a destructive confirmation. */
const DANGER_GHOST_BTN = `${GHOST_BTN} text-j-error hover:text-j-error`;

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

const STATUS_LABELS: Record<string, string> = {
  valid: "Connected",
  unreachable: "Unreachable",
  invalid: "Invalid credential",
  untested: "Untested",
};

/** Three states, derived once and used by both the dot and the chip so the two
 *  can never disagree. `degraded` is configured-but-not-valid: there IS a
 *  credential, it just did not answer. */
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
 * A rule ELEMENT, not a border on the row: a border on the last row would cut
 * across the containing card's rounded corner. The parent renders the list, so
 * it interleaves these — which also means no rule appears above the first row
 * or below the last without a row having to know its own index.
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
   *  API-shaped prop on a component that calls no API. It also means an
   *  uncatalogued provider — which has no schema and so no form — simply gets
   *  no children, instead of the row special-casing what it renders. */
  children?: ReactNode;
}

/**
 * One provider in the Providers tab: connection state on the left, the actions
 * that are actually meaningful for that state on the right.
 *
 * The action set is not cosmetic. `Remove` exists only for `source ===
 * "workspace"` because DELETE removes the workspace credential row and nothing
 * else — offering it for an env-backed or deployment-default credential is a
 * button that appears to work and changes nothing.
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
  const ownedHere = status.source === "workspace";

  // Connected-but-inherited offers Override (add a workspace key that wins)
  // rather than Edit (change the one you already own).
  const primaryLabel = expanded
    ? "Cancel"
    : !status.configured
      ? "Connect"
      : ownedHere
        ? "Edit"
        : "Override";

  const statusChip = !status.configured ? (
    <Chip variant="outline">Not connected</Chip>
  ) : (
    <Chip variant={connection === "connected" ? "success" : "warning"}>
      {STATUS_LABELS[status.status] ?? status.status}
    </Chip>
  );

  const detail = status.configured
    ? SOURCE_DETAIL[status.source]
    : entry && entry.model_count > 0
      ? `${entry.model_count} models`
      : "";

  return (
    <div
      className={`border-l-2 ${
        expanded ? "border-j-primary bg-j-primary/5" : "border-transparent"
      }`}
    >
      <div className="flex items-center gap-[13px] py-[11px] px-[20px]">
        <StatusDot connection={connection} />

        <span className="w-[150px] shrink-0 text-[14px] font-medium text-t-primary truncate">
          {name}
        </span>

        {entry && <Chip>{AUTH_KIND_LABELS[entry.auth_kind]}</Chip>}
        {statusChip}
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

        <div className="flex items-center gap-[7px] shrink-0">
          {!uncatalogued && status.configured && (
            <button type="button" disabled={busy} onClick={onTest} className={GHOST_BTN}>
              Test
            </button>
          )}
          {!uncatalogued && (
            <button
              type="button"
              disabled={busy}
              onClick={onToggle}
              aria-expanded={expanded}
              className={GHOST_BTN}
            >
              {primaryLabel}
            </button>
          )}
          {/* Only a credential this workspace owns is this workspace's to delete. */}
          {ownedHere && (
            <button
              type="button"
              disabled={busy}
              onClick={onRemove}
              className={DANGER_GHOST_BTN}
            >
              Remove
            </button>
          )}
        </div>
      </div>

      {expanded && children}
    </div>
  );
}
