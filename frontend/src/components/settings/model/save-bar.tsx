"use client";

import { btn } from "../controls";

export interface SaveBarProps {
  /**
   * The changed scopes, already resolved to display names ("Reasoning",
   * "Planner") and already ordered. Its LENGTH is the count — a separate
   * `count` prop would be a second number that could disagree with the list
   * beside it.
   */
  changed: readonly string[];
  saving?: boolean;
  onDiscard: () => void;
  onSave: () => void;
}

/**
 * The Model tab's one save affordance (§9.7) — defects **F2** and **F3**.
 *
 * **F2 was two save buttons**, one under the tiers and one under the overrides,
 * both calling the same `PUT` with the same whole-draft body. Either one saved
 * everything, so "Save overrides" was a lie about scope and the pair was a lie
 * about there being a choice. There is exactly one button here, and it persists
 * tiers and overrides together because that is what the request has always done.
 *
 * **F3 was a save with no state**: nothing said whether anything had changed, so
 * the button was always live and a founder could not tell a saved tab from an
 * unsaved one. The bar states the count and disables both actions when clean.
 *
 * **It NAMES the changed scopes, and that is load-bearing.** §9.6 makes a
 * warned card substitute its meta row, so a card that is both warned and dirty
 * loses its "Changed — not saved" marker; on a three-card stack, "1 unsaved
 * change" plus a subtle ring is the weakest signal on the surface. The names
 * close that here rather than by adding a row back to the card, which would
 * reflow the whole stack the moment a credential was revoked.
 */
export function SaveBar({
  changed,
  saving = false,
  onDiscard,
  onSave,
}: SaveBarProps) {
  const count = changed.length;
  const clean = count === 0;

  return (
    <section
      aria-label="Save model configuration"
      // `sticky`, not `fixed`: the tab renders INSIDE the shell's scroll
      // container, so this is as far outside the scrolling content as a tab can
      // put itself without reaching into `settings-modal.tsx`. The negative
      // inline margins bleed it to the panel edges through the shell's padding.
      className={
        "sticky bottom-0 z-10 -mx-4 sm:-mx-6 mt-auto " +
        "border-t border-b-secondary bg-surface-2/50 backdrop-blur-sm " +
        "px-[24px] py-[12px] flex items-center gap-3"
      }
    >
      {clean ? (
        <p className="text-[12.5px] text-t-muted">No changes</p>
      ) : (
        <p className="min-w-0 text-[12.5px] text-j-primary flex items-center gap-[6px]">
          <span
            aria-hidden="true"
            className="w-[6px] h-[6px] rounded-full bg-j-primary shrink-0"
          />
          <span className="truncate">
            {count} unsaved change{count === 1 ? "" : "s"}
            {" — "}
            {changed.join(", ")}
          </span>
        </p>
      )}

      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          disabled={clean || saving}
          onClick={onDiscard}
          className={btn({ size: "md" })}
        >
          Discard
        </button>
        <button
          type="button"
          disabled={clean || saving}
          onClick={onSave}
          className={btn({ size: "md", variant: "primary" })}
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </section>
  );
}
