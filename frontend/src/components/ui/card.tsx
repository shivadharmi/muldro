import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
  variant = "default",
}: {
  children: ReactNode;
  className?: string;
  variant?: "default" | "live" | "warning" | "error";
}) {
  const base = "rounded-[var(--radius-lg)]";
  const variants: Record<string, string> = {
    default: "bg-surface-1 border border-b-secondary",
    live: "bg-surface-1 border border-j-primary/30 shadow-[var(--shadow-glow)]",
    warning: "bg-j-warning-soft border border-j-warning/20",
    error: "bg-j-error-soft border border-j-error/20",
  };

  return (
    <div className={`${base} ${variants[variant] || variants.default} ${className}`}>
      {children}
    </div>
  );
}

export function CardBody({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`px-5 py-4 ${className}`}>{children}</div>;
}
