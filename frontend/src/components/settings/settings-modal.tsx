"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/lib/auth";
import {
  useSettingsModalStore,
  type SettingsTab,
} from "@/stores/settings-modal-store";
import { useFocusTrap } from "./hooks/use-focus-trap";
import { ModelConfigProvider, useProviderCounts } from "./model-config-context";
import { SETTINGS_TITLE_ID, SettingsRail, tabMetaFor } from "./settings-rail";
import { AccountTab } from "./tabs/account-tab";
import { ModelTab } from "./tabs/model-tab";
import { PolicyTab } from "./tabs/policy-tab";
import { PreferencesTab } from "./tabs/preferences-tab";
import { ProvidersTab } from "./tabs/providers-tab";
import { SpendingTab } from "./tabs/spending-tab";
import { TrustTab } from "./tabs/trust-tab";

/** Chevron left (§9.11) — the mobile back affordance. */
function ChevronLeft() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M8.5 3L4.5 7l4 4" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M4 4l8 8M12 4l-8 8"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * Routes the active tab to its component. Every tab owns its own data — this
 * shell hands none of them any (defect L5), so a tab that is not on screen
 * fetches nothing.
 */
function TabBody({ tab }: { tab: SettingsTab }) {
  const { user, logout } = useAuth();
  switch (tab) {
    case "account":
      return (
        <AccountTab
          email={user?.email ?? null}
          displayName={user?.display_name ?? null}
          onSignOut={logout}
        />
      );
    case "preferences":
      return <PreferencesTab />;
    case "policy":
      return <PolicyTab />;
    case "budget":
      return <SpendingTab />;
    case "trust":
      return <TrustTab />;
    case "model":
      return <ModelTab />;
    case "providers":
      return <ProvidersTab />;
    default:
      return null;
  }
}

/**
 * The dialog proper. Mounted only while open, so the focus trap's capture and
 * restore ride on its mount/unmount rather than on a prop.
 */
function SettingsDialog() {
  const activeTab = useSettingsModalStore((s) => s.activeTab);
  const setActiveTab = useSettingsModalStore((s) => s.setActiveTab);
  const closeSettings = useSettingsModalStore((s) => s.closeSettings);
  const providerCounts = useProviderCounts();

  // Below `sm` the rail is the sheet's ROOT view and a tab is pushed over it
  // (defect L4). At `sm`+ both panes show side by side and this is ignored.
  const [pushed, setPushed] = useState(false);
  const panelRef = useFocusTrap<HTMLDivElement>(true);
  const meta = tabMetaFor(activeTab);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeSettings();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [closeSettings]);

  return (
    <div
      className="fixed inset-0 z-50 flex sm:items-center sm:justify-center animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby={SETTINGS_TITLE_ID}
    >
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={closeSettings}
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        className="relative flex flex-col sm:flex-row overflow-hidden w-full h-full min-h-0 bg-surface-1 outline-none animate-scale-in sm:mx-4 sm:w-full sm:max-w-4xl sm:h-[min(820px,calc(100dvh-4rem))] sm:border sm:border-b-secondary sm:rounded-[var(--radius-xl)] sm:shadow-[var(--shadow-lg)]"
      >
        <SettingsRail
          activeTab={activeTab}
          providerCounts={providerCounts}
          onSelect={(tab) => {
            setActiveTab(tab);
            setPushed(true);
          }}
          className={`${pushed ? "hidden sm:flex" : "flex"} w-full sm:w-[200px]`}
        />

        <div
          className={`${
            pushed ? "flex" : "hidden sm:flex"
          } flex-1 min-w-0 min-h-0 flex-col`}
        >
          <div className="flex items-center gap-2 shrink-0 border-b border-b-secondary py-[14px] pl-[8px] pr-[12px] sm:px-6 sm:py-4">
            <button
              type="button"
              onClick={() => setPushed(false)}
              className="sm:hidden flex items-center gap-0.5 h-11 px-2 text-[15px] text-j-primary rounded-[var(--radius-md)] hover:bg-surface-2 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring"
            >
              <ChevronLeft />
              Settings
            </button>

            <div className="flex-1 min-w-0 text-center sm:text-left">
              <p className="text-base sm:text-[15px] font-semibold text-t-primary truncate">
                {meta.label}
              </p>
              {meta.subtitle && (
                <p className="hidden sm:block text-[12.5px] leading-[1.5] text-t-tertiary mt-[3px]">
                  {meta.subtitle}
                </p>
              )}
            </div>

            <button
              onClick={closeSettings}
              className="shrink-0 flex items-center justify-center h-11 w-11 sm:h-auto sm:w-auto sm:p-1 rounded-[var(--radius-sm)] text-t-muted hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring"
              aria-label="Close settings"
            >
              <CloseIcon />
            </button>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-[18px] bg-surface-0 px-4 py-[18px] sm:bg-transparent sm:px-6 sm:py-5">
            <TabBody tab={activeTab} />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The settings shell: frame, backdrop, rail, header, focus management and
 * routing to a tab — and no tab's data. The model/provider configuration one
 * level up is shared by the Model tab and the rail's connected/total suffix;
 * it lives in `ModelConfigProvider`, not here.
 */
export function SettingsModal() {
  const open = useSettingsModalStore((s) => s.open);
  if (!open) return null;
  return (
    <ModelConfigProvider>
      <SettingsDialog />
    </ModelConfigProvider>
  );
}
