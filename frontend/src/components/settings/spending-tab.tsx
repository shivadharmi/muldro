import { Card, CardBody } from "@/components/ui/card";

interface SpendingTabProps {
  budgetLimit: number | null;
  editing: boolean;
  input: string;
  saving: boolean;
  onEditStart: () => void;
  onInputChange: (value: string) => void;
  onSave: () => void;
  onCancel: () => void;
}

export function SpendingTab({
  budgetLimit,
  editing,
  input,
  saving,
  onEditStart,
  onInputChange,
  onSave,
  onCancel,
}: SpendingTabProps) {
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
                  min="0.01"
                  step="0.01"
                  value={input}
                  onChange={(e) => onInputChange(e.target.value)}
                  className="w-32 rounded-[var(--radius-md)] bg-surface-2 border border-b-secondary px-3 py-2 text-sm text-t-primary focus:outline-none focus:ring-1 focus:ring-j-ring transition-colors"
                  autoFocus
                />
                <button
                  onClick={onSave}
                  disabled={saving}
                  className="px-3.5 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-[13px] font-medium hover:bg-j-primary-hover disabled:opacity-50 transition-colors cursor-pointer"
                >
                  {saving ? "Saving..." : "Save"}
                </button>
                <button
                  onClick={onCancel}
                  className="px-3.5 py-2 rounded-[var(--radius-md)] text-t-secondary text-[13px] hover:bg-surface-2 transition-colors cursor-pointer"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <p className="text-2xl font-semibold text-t-primary tracking-tight">
                  ${budgetLimit?.toFixed(2) ?? "—"}
                  <span className="text-sm text-t-muted font-normal ml-1">/ day</span>
                </p>
                <button
                  onClick={onEditStart}
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
