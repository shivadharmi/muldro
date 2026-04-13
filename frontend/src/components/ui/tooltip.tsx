"use client";

import type { ReactNode } from "react";

interface TooltipProps {
  text: string;
  children: ReactNode;
  position?: "top" | "bottom";
}

export function Tooltip({ text, children, position = "top" }: TooltipProps) {
  if (!text) return <>{children}</>;

  const posClass =
    position === "top"
      ? "bottom-full left-1/2 -translate-x-1/2 mb-2"
      : "top-full left-1/2 -translate-x-1/2 mt-2";

  const arrowClass =
    position === "top"
      ? "top-full left-1/2 -translate-x-1/2 border-t-[#27272a] border-t-[5px] border-x-transparent border-x-[5px] border-b-0"
      : "bottom-full left-1/2 -translate-x-1/2 border-b-[#27272a] border-b-[5px] border-x-transparent border-x-[5px] border-t-0";

  return (
    <span className="relative group inline-flex">
      {children}
      <span
        role="tooltip"
        className={`absolute ${posClass} z-50 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-150 delay-300 whitespace-normal max-w-[280px] px-3 py-2 text-[11px] text-[#d4d4d8] bg-[#27272a] border border-white/10 rounded-[var(--radius-md)] shadow-lg`}
      >
        {text}
        <span className={`absolute ${arrowClass} w-0 h-0`} />
      </span>
    </span>
  );
}
