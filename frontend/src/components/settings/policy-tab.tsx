interface PolicyMode {
  value: string;
  label: string;
  description: string;
}

interface PermissionModeOption {
  value: string;
  label: string;
  description: string;
}

interface PolicyTabProps {
  policyMode: string;
  policyModes: PolicyMode[];
  policyLoading: boolean;
  onPolicyChange: (value: string) => void;
  defaultPermissionMode: string;
  permissionModes: PermissionModeOption[];
  permissionLoading: boolean;
  onDefaultPermissionModeChange: (value: string) => void;
}

export function PolicyTab({
  policyMode,
  policyModes,
  policyLoading,
  onPolicyChange,
  defaultPermissionMode,
  permissionModes,
  permissionLoading,
  onDefaultPermissionModeChange,
}: PolicyTabProps) {
  return (
    <div className="space-y-6">
      <p className="text-sm text-t-tertiary leading-relaxed">
        Your overall posture applies to everything Jarvis does. Fine-tune how much
        Jarvis can do on its own for each kind of action in the Trust tab.
      </p>

      <div>
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Overall posture
        </p>
        <div className="space-y-2">
          {policyModes.map((pm) => {
            const isActive = policyMode === pm.value;
            return (
              <button
                key={pm.value}
                type="button"
                onClick={() => onPolicyChange(pm.value)}
                disabled={policyLoading}
                className={`w-full text-left rounded-[var(--radius-lg)] border p-4 transition-all duration-150 cursor-pointer ${
                  isActive
                    ? "border-j-primary/40 bg-j-primary-soft"
                    : "border-b-secondary bg-surface-1 hover:bg-surface-2"
                } disabled:opacity-50`}
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 ${
                      isActive ? "border-j-primary" : "border-b-strong"
                    }`}
                  >
                    {isActive && <div className="w-2 h-2 rounded-full bg-j-primary" />}
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-t-primary">{pm.label}</p>
                    <p className="text-xs text-t-tertiary mt-0.5">{pm.description}</p>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Default chat permission mode
        </p>
        <p className="text-xs text-t-tertiary mb-3 leading-relaxed">
          The confirmation posture new chat turns start with. You can still change it per turn.
        </p>
        <div className="space-y-2">
          {permissionModes.map((pm) => {
            const isActive = defaultPermissionMode === pm.value;
            return (
              <button
                key={pm.value}
                type="button"
                onClick={() => onDefaultPermissionModeChange(pm.value)}
                disabled={permissionLoading}
                className={`w-full text-left rounded-[var(--radius-lg)] border p-4 transition-all duration-150 cursor-pointer ${
                  isActive
                    ? "border-j-primary/40 bg-j-primary-soft"
                    : "border-b-secondary bg-surface-1 hover:bg-surface-2"
                } disabled:opacity-50`}
              >
                <p className="text-[13px] font-medium text-t-primary">{pm.label}</p>
                <p className="text-xs text-t-tertiary mt-0.5">{pm.description}</p>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
