import Link from "next/link";
import type { ReactNode } from "react";

export function NavItem({
  href,
  label,
  icon,
  active,
  badge,
}: {
  href: string;
  label: string;
  icon: ReactNode;
  active: boolean;
  badge?: number;
}) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2.5 px-2 py-1.5 rounded text-sm transition-colors ${
        active
          ? "bg-neutral-800 text-white"
          : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900"
      }`}
    >
      <span className="flex-shrink-0 w-4 h-4">{icon}</span>
      <span className="flex-1">{label}</span>
      {badge !== undefined && (
        <span className="bg-blue-600 text-white text-[10px] font-medium px-1.5 py-0.5 rounded-full min-w-[18px] text-center">
          {badge}
        </span>
      )}
    </Link>
  );
}
