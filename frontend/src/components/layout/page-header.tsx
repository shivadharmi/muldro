import type { ReactNode } from "react";

type PageVariant = "action" | "monitor" | "config" | "content" | "collection";

const variantStyles: Record<PageVariant, { accent: string; wrapper: string }> = {
  action: {
    accent: "border-l-[3px] border-l-j-warning pl-4",
    wrapper: "",
  },
  monitor: {
    accent: "border-l-[3px] border-l-j-primary pl-4",
    wrapper: "",
  },
  config: {
    accent: "",
    wrapper: "",
  },
  content: {
    accent: "",
    wrapper: "max-w-4xl",
  },
  collection: {
    accent: "",
    wrapper: "",
  },
};

export function PageHeader({
  title,
  subtitle,
  actions,
  variant,
  badge,
  live,
  updatedAt,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  variant?: PageVariant;
  badge?: number;
  live?: boolean;
  updatedAt?: string;
}) {
  const v = variant ? variantStyles[variant] : variantStyles.config;

  return (
    <div className={`flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6 ${v.wrapper}`}>
      <div className={v.accent}>
        <div className="flex items-center gap-2.5">
          <h1 className="text-xl font-semibold text-t-primary tracking-tight">{title}</h1>
          {badge !== undefined && badge > 0 && (
            <span className="bg-j-warning-soft text-j-warning text-[11px] font-semibold px-2 py-0.5 rounded-full">
              {badge}
            </span>
          )}
          {live && (
            <span className="flex items-center gap-1.5 text-[11px] text-j-primary font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-j-primary animate-pulse-live" />
              Live
            </span>
          )}
        </div>
        {subtitle && (
          <p className="text-sm text-t-tertiary mt-0.5">{subtitle}</p>
        )}
        {updatedAt && variant === "monitor" && (
          <p className="text-[11px] text-t-muted mt-0.5">Updated {updatedAt}</p>
        )}
      </div>
      {actions && (
        <div className="flex items-center gap-2 flex-shrink-0">
          {actions}
        </div>
      )}
    </div>
  );
}
