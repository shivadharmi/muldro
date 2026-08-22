"use client";

import { useCallback, useEffect, useState } from "react";

import { Card, CardBody } from "@/components/ui/card";
import { fetchBudget, updateBudgetLimit } from "@/lib/api";
import { errorToMessage } from "@/lib/api-error";
import { useToast } from "@/components/ui/toast";

/**
 * The daily token budget. Owns its own load and save — the settings shell
 * routes to this tab, it does not fetch for it (defect L5).
 */
export function SpendingTab() {
  const { addToast } = useToast();
  const [limit, setLimit] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchBudget()
      .then((r) => {
        if (!cancelled) setLimit(r.daily_limit_usd);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSave = useCallback(async () => {
    const value = parseFloat(input);
    if (isNaN(value) || value <= 0) return;
    setSaving(true);
    try {
      const res = await updateBudgetLimit(value);
      setLimit(res.daily_limit_usd);
      setEditing(false);
      addToast("Budget updated", "success");
    } catch (err) {
      addToast(errorToMessage(err), "error");
    } finally {
      setSaving(false);
    }
  }, [input, addToast]);

  return (
    <Card>
      <CardBody>
        <div className="space-y-4">
          <div>
            <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-3">
              Daily Token Budget
            </p>
            {editing ? (
              <div className="flex items-center gap-2">
                <span className="text-t-secondary text-sm">$</span>
                <input
                  type="number"
                  aria-label="daily budget limit"
                  min="0.01"
                  step="0.01"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  className="w-32 rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary px-3 py-2 text-sm text-t-primary transition-colors"
                  autoFocus
                />
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-3.5 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-[13px] font-medium hover:bg-j-primary-hover disabled:opacity-50 transition-colors cursor-pointer"
                >
                  {saving ? "Saving..." : "Save"}
                </button>
                <button
                  onClick={() => setEditing(false)}
                  className="px-3.5 py-2 rounded-[var(--radius-md)] text-t-secondary text-[13px] hover:bg-surface-2 transition-colors cursor-pointer"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <p className="text-2xl font-semibold text-t-primary tracking-tight tabular-nums">
                  ${limit?.toFixed(2) ?? "—"}
                  <span className="text-sm text-t-muted font-normal ml-1">/ day</span>
                </p>
                <button
                  onClick={() => {
                    setInput(String(limit ?? 5));
                    setEditing(true);
                  }}
                  className="text-xs text-j-primary hover:text-j-primary-hover font-medium cursor-pointer"
                >
                  Edit
                </button>
              </div>
            )}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
