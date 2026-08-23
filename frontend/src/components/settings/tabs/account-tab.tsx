import { Card, CardBody } from "@/components/ui/card";

interface AccountTabProps {
  email: string | null;
  displayName: string | null;
  onSignOut: () => void;
}

export function AccountTab({ email, displayName, onSignOut }: AccountTabProps) {
  return (
    <Card>
      <CardBody>
        <div className="space-y-5">
          <div className="grid grid-cols-[120px_1fr] gap-y-4 gap-x-4 items-baseline">
            <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider">Email</p>
            <p className="text-sm text-t-primary">{email ?? "—"}</p>
            <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider">
              Display Name
            </p>
            <p className="text-sm text-t-primary">{displayName ?? "—"}</p>
          </div>
          <div className="pt-4 border-t border-b-secondary">
            <button
              onClick={onSignOut}
              className="px-4 py-2 rounded-[var(--radius-md)] border border-j-error/30 text-j-error text-[13px] font-medium hover:bg-j-error-soft transition-colors cursor-pointer"
            >
              Sign Out
            </button>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
