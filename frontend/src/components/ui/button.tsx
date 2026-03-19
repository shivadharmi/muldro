import type { ButtonHTMLAttributes, ReactNode } from "react";

const VARIANTS: Record<string, string> = {
  primary:
    "bg-j-primary hover:bg-j-primary-hover text-j-primary-fg shadow-[var(--shadow-sm)] focus:shadow-[var(--shadow-glow)]",
  secondary:
    "bg-surface-3 hover:bg-surface-4 text-t-primary border border-b-primary",
  ghost:
    "hover:bg-j-primary-soft text-t-secondary hover:text-t-primary",
  danger:
    "bg-j-error hover:opacity-90 text-j-primary-fg",
};

export function Button({
  children,
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: {
  children: ReactNode;
  variant?: keyof typeof VARIANTS;
  size?: "sm" | "md" | "lg";
  className?: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const sizeClass =
    size === "sm"
      ? "px-2.5 py-1 text-xs rounded-[var(--radius-sm)]"
      : size === "lg"
        ? "px-5 py-2.5 text-base rounded-[var(--radius-lg)]"
        : "px-3.5 py-1.5 text-sm rounded-[var(--radius-md)]";

  return (
    <button
      className={`inline-flex items-center justify-center font-medium transition-all disabled:opacity-50 disabled:pointer-events-none cursor-pointer focus:outline-none focus:ring-2 focus:ring-j-ring focus:ring-offset-1 focus:ring-offset-surface-0 ${VARIANTS[variant] || VARIANTS.primary} ${sizeClass} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
