import Link from "next/link";
import type { ReactNode } from "react";

export function NavItem({
  href,
  label,
  icon,
  active,
  badge,
  collapsed,
}: {
  href: string;
  label: string;
  icon: ReactNode;
  active: boolean;
  badge?: number;
  collapsed?: boolean;
}) {
  return (
    <Link
      href={href}
      title={collapsed ? label : undefined}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-2.5 rounded-[var(--radius-md)] text-sm transition-all group relative ${
        collapsed ? "justify-center px-2 py-2" : "px-2.5 py-1.5"
      } ${
        active
          ? "bg-j-primary-soft text-j-primary font-medium border-l-2 border-l-j-primary"
          : "text-t-secondary hover:text-t-primary hover:bg-surface-2 border-l-2 border-l-transparent"
      }`}
    >
      <span className="flex-shrink-0 w-4 h-4">{icon}</span>
      {!collapsed && <span className="flex-1 truncate">{label}</span>}
      {badge !== undefined && badge > 0 && (
        <span
          className={`bg-j-primary text-j-primary-fg text-[10px] font-semibold rounded-full min-w-[18px] text-center leading-[18px] ${
            collapsed
              ? "absolute -top-0.5 -right-0.5 px-1"
              : "px-1.5"
          }`}
        >
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </Link>
  );
}
