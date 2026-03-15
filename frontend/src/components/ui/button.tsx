import type { ButtonHTMLAttributes, ReactNode } from "react";

const VARIANTS: Record<string, string> = {
  primary: "bg-blue-600 hover:bg-blue-700 text-white",
  secondary: "bg-neutral-800 hover:bg-neutral-700 text-neutral-200 border border-neutral-700",
  danger: "bg-red-600 hover:bg-red-700 text-white",
  ghost: "hover:bg-neutral-800 text-neutral-400 hover:text-neutral-200",
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
  size?: "sm" | "md";
  className?: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const sizeClass = size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-1.5 text-sm";
  return (
    <button
      className={`inline-flex items-center justify-center rounded font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none ${VARIANTS[variant] || VARIANTS.primary} ${sizeClass} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
