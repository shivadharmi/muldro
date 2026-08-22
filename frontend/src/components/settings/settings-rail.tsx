"use client";

import { memo } from "react";

import type { SettingsTab } from "@/stores/settings-modal-store";
import type { ProviderCounts } from "./model-config-context";

/** The dialog is labelled by the visible heading this id sits on (defect A2). */
export const SETTINGS_TITLE_ID = "settings-modal-title";

export interface SettingsTabMeta {
  key: SettingsTab;
  label: string;
  /** Rendered under the header title. Optional per §9.4. */
  subtitle?: string;
}

/** Rail order. `providers` sits directly after `model` — the two are read together. */
export const SETTINGS_TABS: readonly SettingsTabMeta[] = [
  { key: "account", label: "Account", subtitle: "Your identity and session" },
  {
    key: "preferences",
    label: "Preferences",
    subtitle: "Theme and how Muldro talks to you",
  },
  {
    key: "policy",
    label: "Policy",
    subtitle: "How much Muldro may do on its own",
  },
  { key: "budget", label: "Budget", subtitle: "Your daily spend ceiling" },
  {
    key: "trust",
    label: "Trust",
    subtitle: "Per-capability autonomy, earned over time",
  },
  {
    key: "model",
    label: "Model",
    subtitle: "Which model powers each reasoning tier",
  },
  {
    key: "providers",
    label: "Providers",
    subtitle: "API keys and endpoints Muldro may call",
  },
];

export function tabMetaFor(tab: SettingsTab): SettingsTabMeta {
  return SETTINGS_TABS.find((t) => t.key === tab) ?? SETTINGS_TABS[0];
}

/**
 * Inline stroke-SVG icon per settings tab. Matches the design iconography
 * (§9.11): 16px, viewBox 0 0 16 16, 1.4px strokes, currentColor, round caps.
 * No icon library.
 */
export function TabIcon({ tab }: { tab: SettingsTab }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (tab) {
    case "account": // person
      return (
        <svg {...common}>
          <circle cx="8" cy="5" r="2.5" />
          <path d="M3 13c0-2.5 2.2-4 5-4s5 1.5 5 4" />
        </svg>
      );
    case "preferences": // sliders
      return (
        <svg {...common}>
          <path d="M3 5h6M11 5h2M3 11h2M7 11h6" />
          <circle cx="10" cy="5" r="1.4" />
          <circle cx="6" cy="11" r="1.4" />
        </svg>
      );
    case "policy": // shield
      return (
        <svg {...common}>
          <path d="M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4l5-2z" />
        </svg>
      );
    case "budget": // coin / dollar
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6" />
          <path d="M8 4.5v7M9.8 6c-.4-.7-1.1-1-1.8-1-1 0-1.7.6-1.7 1.4 0 1.9 3.5 1 3.5 2.9 0 .8-.8 1.4-1.8 1.4-.8 0-1.5-.3-1.9-1" />
        </svg>
      );
    case "trust": // verified badge (check in circle)
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6" />
          <path d="M5.5 8.2l1.7 1.7 3.3-3.6" />
        </svg>
      );
    case "model": // chip / CPU
      return (
        <svg {...common}>
          <rect x="5" y="5" width="6" height="6" rx="1" />
          <path d="M6.5 2.5v2M9.5 2.5v2M6.5 11.5v2M9.5 11.5v2M2.5 6.5h2M2.5 9.5h2M11.5 6.5h2M11.5 9.5h2" />
        </svg>
      );
    case "providers": // plug
      return (
        <svg {...common}>
          <path d="M5.5 2.5v3M10.5 2.5v3" />
          <path d="M3.5 5.5h9v2a4.5 4.5 0 01-9 0v-2z" />
          <path d="M8 12v1.5" />
        </svg>
      );
    default:
      return null;
  }
}

interface SettingsRailProps {
  activeTab: SettingsTab;
  onSelect: (tab: SettingsTab) => void;
  /** `null` while the model config has not loaded — the suffix is then omitted
   *  entirely rather than rendering a misleading `0/0`. */
  providerCounts?: ProviderCounts | null;
  className?: string;
}

/**
 * The settings tab list. At `sm`+ it is the 200px left rail; below `sm` it is
 * the sheet's root view — a full-width push list, never a horizontal scroller
 * (defect L4). Which of the two it is, is the caller's layout decision.
 *
 * Memoised: it renders seven inline SVGs, and the shell above it re-renders for
 * reasons that have nothing to do with the tab list.
 */
export const SettingsRail = memo(function SettingsRail({
  activeTab,
  onSelect,
  providerCounts,
  className = "",
}: SettingsRailProps) {
  return (
    <nav
      aria-label="Settings sections"
      className={`flex flex-col shrink-0 gap-[2px] p-[10px] min-h-0 overflow-y-auto border-b-secondary w-full sm:w-[200px] sm:border-r bg-surface-2/40 ${className}`}
    >
      <h2
        id={SETTINGS_TITLE_ID}
        className="px-[10px] pt-[8px] pb-[12px] text-[15px] font-semibold text-t-primary"
      >
        Settings
      </h2>

      {SETTINGS_TABS.map((tab) => {
        const isActive = activeTab === tab.key;
        const counts = tab.key === "providers" ? providerCounts : null;
        return (
          <button
            key={tab.key}
            type="button"
            onClick={() => onSelect(tab.key)}
            aria-current={isActive ? "true" : undefined}
            className={`flex items-center gap-[10px] text-left rounded-[8px] px-[12px] py-[7px] text-[13px] whitespace-nowrap transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring ${
              isActive
                ? "bg-j-primary-soft text-j-primary font-medium"
                : "text-t-tertiary hover:text-t-primary hover:bg-surface-2"
            }`}
          >
            <span className="shrink-0">
              <TabIcon tab={tab.key} />
            </span>
            {tab.label}
            {counts && (
              <span className="ml-auto text-[11px] text-t-muted tabular-nums">
                {counts.connected}/{counts.total}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
});
