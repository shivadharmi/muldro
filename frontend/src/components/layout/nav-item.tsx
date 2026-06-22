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
      className={`flex items-center gap-2.5 rounded-[var(--radius-md)] text-[13px] transition-all duration-150 group relative ${
        collapsed ? "justify-center p-2.5" : "px-3 py-2"
      } ${
        active
          ? "bg-j-primary-soft text-j-primary font-medium"
          : "text-t-tertiary hover:text-t-primary hover:bg-surface-2"
      }`}
    >
      <span className="flex-shrink-0 w-[18px] h-[18px]">{icon}</span>
      {!collapsed && <span className="flex-1 truncate">{label}</span>}
      {active && !collapsed && (
        <span
          aria-hidden="true"
          className="flex-shrink-0 w-1.5 h-1.5 rounded-full bg-j-primary"
        />
      )}
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
