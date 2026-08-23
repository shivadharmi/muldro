const VARIANTS: Record<string, string> = {
  default: "bg-surface-3 text-t-secondary border border-b-secondary",
  live: "bg-j-primary-soft text-j-primary border border-j-primary/30",
  success: "bg-j-success-soft text-j-success border border-j-success/30",
  warning: "bg-j-warning-soft text-j-warning border border-j-warning/30",
  error: "bg-j-error-soft text-j-error border border-j-error/30",
  info: "bg-j-info-soft text-j-info border border-j-info/30",
  secondary: "bg-j-secondary-soft text-j-secondary border border-j-secondary/30",
  // Legacy compat aliases
  blue: "bg-j-info-soft text-j-info border border-j-info/30",
  green: "bg-j-success-soft text-j-success border border-j-success/30",
  yellow: "bg-j-warning-soft text-j-warning border border-j-warning/30",
  red: "bg-j-error-soft text-j-error border border-j-error/30",
  purple: "bg-j-secondary-soft text-j-secondary border border-j-secondary/30",
};

export function Badge({
  children,
  variant = "default",
  className = "",
}: {
  children: React.ReactNode;
  variant?: keyof typeof VARIANTS;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${VARIANTS[variant] || VARIANTS.default} ${className}`}
    >
      {children}
    </span>
  );
}
