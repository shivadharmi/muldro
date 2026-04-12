import type { ButtonHTMLAttributes, ReactNode } from "react";

const VARIANTS: Record<string, string> = {
  primary:
    "bg-j-primary hover:bg-j-primary-hover text-j-primary-fg shadow-[var(--shadow-sm)]",
  secondary:
    "bg-surface-2 hover:bg-surface-3 text-t-primary border border-b-secondary",
  ghost:
    "hover:bg-surface-2 text-t-secondary hover:text-t-primary",
  danger:
    "bg-j-error hover:opacity-90 text-white",
  outline:
    "border border-b-secondary text-t-secondary hover:text-t-primary hover:bg-surface-2",
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
      ? "px-2.5 py-1 text-xs gap-1.5 rounded-[var(--radius-sm)]"
      : size === "lg"
        ? "px-5 py-2.5 text-[15px] gap-2 rounded-[var(--radius-lg)]"
        : "px-3.5 py-2 text-[13px] gap-2 rounded-[var(--radius-md)]";

  return (
    <button
      className={`inline-flex items-center justify-center font-medium transition-all duration-150 disabled:opacity-50 disabled:pointer-events-none cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring focus-visible:ring-offset-1 focus-visible:ring-offset-surface-0 ${VARIANTS[variant] || VARIANTS.primary} ${sizeClass} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
