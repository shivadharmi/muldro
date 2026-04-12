import type { ReactNode } from "react";

export function EmptyState({
  title,
  description,
  icon,
  action,
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="text-center py-16 px-6">
      {icon && (
        <div className="flex justify-center mb-4 text-t-muted">{icon}</div>
      )}
      <p className="text-t-secondary text-[15px] font-medium">{title}</p>
      {description && (
        <p className="text-t-muted text-sm mt-1.5 max-w-md mx-auto leading-relaxed">
          {description}
        </p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
