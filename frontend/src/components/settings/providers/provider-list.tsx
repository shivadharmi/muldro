"use client";

import { Fragment } from "react";

import type { ExpandedRow } from "../hooks/use-expanded-row";
import type { CredentialFields } from "../hooks/use-provider-credentials";
import { ProviderCredentialForm } from "./provider-credential-form";
import type { ProviderEntry } from "./provider-entries";
import { ProviderRow, ProviderRowSeparator } from "./provider-row";
import { RemoveConfirmation, type PendingRemoval } from "./remove-confirmation";
import { ROW_ANCHOR, rowAnchorAttrs } from "./row-anchor";

export interface ProviderListProps {
  /** The rows to render, already filtered and grouped by the tab. */
  entries: readonly ProviderEntry[];
  /** The one open row across ALL groups — exclusivity is the tab's to own, and
   *  a list rendering half the providers could not enforce it. */
  expanded: ExpandedRow | null;
  /** The removal awaiting an answer, interleaved under its own row when it is
   *  one of these. */
  pending: PendingRemoval | null;
  isBusy: (provider: string) => boolean;
  onOpen: (provider: string) => void;
  onClose: () => void;
  onTest: (provider: string, name: string) => void;
  onRemove: (provider: string, name: string) => void;
  onSave: (
    provider: string,
    name: string,
    fields: CredentialFields,
  ) => Promise<void>;
  onCancelRemoval: (provider: string) => void;
  onConfirmRemoval: () => void;
}

/**
 * A run of provider rows — one group of the Providers tab.
 *
 * Holds no state: which row is open, which removal is pending and every call
 * that changes either belong to the tab, because both facts are true across the
 * Connected and Available groups and neither list can see the other.
 *
 * The confirmation is interleaved directly beneath its own row, so reading
 * order, tab order and the thing being answered for are all the same place.
 */
export function ProviderList({
  entries,
  expanded,
  pending,
  isBusy,
  onOpen,
  onClose,
  onTest,
  onRemove,
  onSave,
  onCancelRemoval,
  onConfirmRemoval,
}: ProviderListProps) {
  return (
    <>
      {entries.map((item, index) => {
        const { status, entry } = item;
        const provider = status.provider;
        const name = entry?.display_name ?? provider;
        const open = expanded?.provider === provider;
        const busy = isBusy(provider);
        return (
          <Fragment key={provider}>
            {index > 0 && <ProviderRowSeparator />}
            <div {...rowAnchorAttrs(provider)} tabIndex={-1} className={ROW_ANCHOR}>
              <ProviderRow
                status={status}
                catalog={entry}
                expanded={open}
                busy={busy}
                // Whose reason this is. WHETHER a reason may show is the row's
                // own call, gated there on `expanded` — put in the component
                // that holds the data rather than trusted to every caller.
                reason={open ? expanded?.reason : undefined}
                onToggle={() => (open ? onClose() : onOpen(provider))}
                onTest={() => onTest(provider, name)}
                onRemove={() => onRemove(provider, name)}
              >
                {/* An uncatalogued provider declares no credential schema, so it
                    gets no form rather than a zero-field one, and `ProviderRow`
                    withholds the body for the same reason. An intent naming one
                    STILL expands it and still shows the reason — its only action
                    is Remove, and a founder sent somewhere that cannot be
                    connected has to be told why. Dropping the intent would land
                    them on an unchanged list. */}
                {entry && (
                  <ProviderCredentialForm
                    provider={entry}
                    status={status}
                    busy={busy}
                    onSubmit={(fields) => onSave(provider, name, fields)}
                  />
                )}
              </ProviderRow>
            </div>
            {pending?.provider === provider && (
              <>
                <ProviderRowSeparator />
                <RemoveConfirmation
                  pending={pending}
                  onCancel={() => onCancelRemoval(provider)}
                  onConfirm={onConfirmRemoval}
                />
              </>
            )}
          </Fragment>
        );
      })}
    </>
  );
}
