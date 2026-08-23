"use client";

import { useCallback, useEffect, useState } from "react";

import {
  fetchPolicyMode,
  fetchWorkspaceDefaultPermissionMode,
  setPolicyMode,
  setWorkspaceDefaultPermissionMode,
} from "@/lib/api";
import { errorToMessage } from "@/lib/api-error";
import { useToast } from "@/components/ui/toast";

interface ModeOption {
  value: string;
  label: string;
  description: string;
}

const POLICY_MODES: readonly ModeOption[] = [
  { value: "lockdown", label: "Lockdown", description: "All actions blocked" },
  {
    value: "approval_required",
    label: "Approval Required",
    description: "All actions need approval",
  },
  {
    value: "suggest_only",
    label: "Suggest Only",
    description: "Muldro suggests, never acts",
  },
  {
    value: "full_auto",
    label: "Full Auto",
    description: "Muldro acts autonomously",
  },
];

const PERMISSION_MODES: readonly ModeOption[] = [
  { value: "auto", label: "Auto", description: "Confirm only risky writes" },
  { value: "ask", label: "Ask", description: "Confirm every write" },
  {
    value: "bypass",
    label: "Bypass",
    description: "Never confirm (requires workspace entitlement)",
  },
];

interface ModeListProps {
  options: readonly ModeOption[];
  selected: string;
  disabled: boolean;
  onSelect: (value: string) => void;
  /** The posture list carries a radio affordance; the permission list does not. */
  withRadio?: boolean;
}

function ModeList({
  options,
  selected,
  disabled,
  onSelect,
  withRadio = false,
}: ModeListProps) {
  return (
    <div className="space-y-2">
      {options.map((option) => {
        const isActive = selected === option.value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onSelect(option.value)}
            disabled={disabled}
            aria-pressed={isActive}
            className={`w-full text-left rounded-[var(--radius-lg)] border p-4 transition-all duration-150 cursor-pointer ${
              isActive
                ? "border-j-primary/40 bg-j-primary-soft"
                : "border-b-secondary bg-surface-1 hover:bg-surface-2"
            } disabled:opacity-50`}
          >
            <div className="flex items-center gap-3">
              {withRadio && (
                <div
                  className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 ${
                    isActive ? "border-j-primary" : "border-b-strong"
                  }`}
                >
                  {isActive && (
                    <div className="w-2 h-2 rounded-full bg-j-primary" />
                  )}
                </div>
              )}
              <div>
                <p className="text-[13px] font-medium text-t-primary">
                  {option.label}
                </p>
                <p className="text-xs text-t-tertiary mt-0.5">
                  {option.description}
                </p>
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

/**
 * Workspace posture + the default chat permission mode. Owns both, including
 * their loads and their saves: the settings shell routes to this tab, it does
 * not fetch for it (defect L5).
 */
export function PolicyTab() {
  const { addToast } = useToast();
  const [policyMode, setPolicyModeState] = useState("approval_required");
  const [policyLoading, setPolicyLoading] = useState(false);
  const [permissionMode, setPermissionModeState] = useState("auto");
  const [permissionLoading, setPermissionLoading] = useState(false);

  // Read-only hydration. A failure leaves the safe defaults above on screen
  // rather than a blank selection, so it is not toasted.
  useEffect(() => {
    let cancelled = false;
    fetchPolicyMode()
      .then((r) => {
        if (!cancelled) setPolicyModeState(r.mode);
      })
      .catch(() => {});
    fetchWorkspaceDefaultPermissionMode()
      .then((r) => {
        if (!cancelled) setPermissionModeState(r.default_permission_mode);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const handlePolicyChange = useCallback(
    async (mode: string) => {
      setPolicyLoading(true);
      try {
        await setPolicyMode(mode);
        setPolicyModeState(mode);
        addToast("Policy mode updated", "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setPolicyLoading(false);
      }
    },
    [addToast],
  );

  const handlePermissionChange = useCallback(
    async (mode: string) => {
      setPermissionLoading(true);
      try {
        await setWorkspaceDefaultPermissionMode(mode);
        setPermissionModeState(mode);
        addToast("Default permission mode updated", "success");
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setPermissionLoading(false);
      }
    },
    [addToast],
  );

  return (
    <div className="space-y-6">
      <p className="text-sm text-t-tertiary leading-relaxed">
        Your overall posture applies to everything Muldro does. Fine-tune how much
        Muldro can do on its own for each kind of action in the Trust tab.
      </p>

      <div>
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Overall posture
        </p>
        <ModeList
          options={POLICY_MODES}
          selected={policyMode}
          disabled={policyLoading}
          onSelect={handlePolicyChange}
          withRadio
        />
      </div>

      <div>
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Default chat permission mode
        </p>
        <p className="text-xs text-t-tertiary mb-3 leading-relaxed">
          The confirmation posture new chat turns start with. You can still change it per turn.
        </p>
        <ModeList
          options={PERMISSION_MODES}
          selected={permissionMode}
          disabled={permissionLoading}
          onSelect={handlePermissionChange}
        />
      </div>
    </div>
  );
}
