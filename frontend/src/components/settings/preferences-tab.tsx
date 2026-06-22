"use client";

import { Card, CardBody } from "@/components/ui/card";
import { useTheme } from "@/lib/theme";

type Theme = "light" | "dark" | "system";

const THEME_OPTIONS: Array<{ value: Theme; label: string; description: string }> = [
  { value: "light", label: "Light", description: "Always light" },
  { value: "dark", label: "Dark", description: "Always dark" },
  { value: "system", label: "System", description: "Match your OS" },
];

export function PreferencesTab() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="space-y-6">
      {/* Theme — fully wired (persisted to localStorage via ThemeProvider) */}
      <div>
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Theme
        </p>
        <div className="grid grid-cols-3 gap-2">
          {THEME_OPTIONS.map((opt) => {
            const isActive = theme === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => setTheme(opt.value)}
                aria-pressed={isActive}
                className={`text-left rounded-[var(--radius-lg)] border p-3 transition-all duration-150 cursor-pointer ${
                  isActive
                    ? "border-j-primary/40 bg-j-primary-soft"
                    : "border-b-secondary bg-surface-1 hover:bg-surface-2"
                }`}
              >
                <p className="text-[13px] font-medium text-t-primary">{opt.label}</p>
                <p className="text-xs text-t-tertiary mt-0.5">{opt.description}</p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Communication style — not yet persisted; honest coming-state */}
      <div className="border-t border-b-secondary pt-5">
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Communication style
        </p>
        <Card>
          <CardBody>
            <div className="py-2">
              <p className="text-sm text-t-secondary font-medium mb-1">
                Jarvis is learning how you like to be talked to
              </p>
              <p className="text-xs text-t-muted leading-relaxed">
                Tone, brevity, and formatting preferences are inferred from how you
                chat and react. Explicit controls will live here once they are
                wired to a backend.
              </p>
            </div>
          </CardBody>
        </Card>
      </div>

      {/* Notifications — not yet persisted; honest coming-state */}
      <div className="border-t border-b-secondary pt-5">
        <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
          Notifications
        </p>
        <Card>
          <CardBody>
            <div className="py-2">
              <p className="text-sm text-t-secondary font-medium mb-1">
                Delivery preferences are managed automatically
              </p>
              <p className="text-xs text-t-muted leading-relaxed">
                Jarvis rate-limits and prioritizes alerts on its own for now.
                Per-surface controls will appear here when exposed.
              </p>
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
