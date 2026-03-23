"use client";

import { usePathname } from "next/navigation";
import { NavItem } from "./nav-item";
import { useTheme } from "@/lib/theme";

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();
  const { theme, setTheme, resolved } = useTheme();

  const cycleTheme = () => {
    const order: Array<"light" | "dark" | "system"> = ["light", "dark", "system"];
    const idx = order.indexOf(theme);
    setTheme(order[(idx + 1) % order.length]);
  };

  const themeIcon =
    resolved === "dark" ? (
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
        <path
          d="M13.5 8.5a5.5 5.5 0 01-6-6A5.5 5.5 0 108.5 14a5.5 5.5 0 005-5.5z"
          stroke="currentColor"
          strokeWidth="1.2"
        />
      </svg>
    ) : (
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.2" />
        <path
          d="M8 2v1.5M8 12.5V14M2 8h1.5M12.5 8H14M3.8 3.8l1 1M11.2 11.2l1 1M3.8 12.2l1-1M11.2 4.8l1-1"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
        />
      </svg>
    );

  return (
    <aside
      className={`flex-shrink-0 border-r border-b-secondary bg-surface-1 flex flex-col h-screen transition-[width] duration-200 ${
        collapsed ? "w-16" : "w-60"
      }`}
    >
      {/* Header with brand */}
      <div className="px-3 py-3 border-b border-b-secondary flex items-center gap-2">
        <button
          onClick={onToggle}
          className="p-1.5 rounded-[var(--radius-sm)] hover:bg-surface-2 text-t-tertiary hover:text-t-primary transition-colors cursor-pointer"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
            {collapsed ? (
              <path
                d="M3 5h12M3 9h12M3 13h12"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            ) : (
              <path
                d="M4 5h10M4 9h8M4 13h6"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            )}
          </svg>
        </button>
        {!collapsed && (
          <div className="animate-fade-in">
            <h1 className="text-sm font-bold tracking-tight brand-gradient-text">
              Jarvis
            </h1>
            <p className="text-[9px] text-t-muted leading-none">AI Operating System</p>
          </div>
        )}
      </div>

      {/* Navigation — flat list */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
        <NavItem
          href="/"
          label="Workspace"
          active={pathname === "/"}
          collapsed={collapsed}
          icon={
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
              <rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
              <rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
              <rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
            </svg>
          }
        />
        <NavItem
          href="/chat"
          label="Chat"
          active={pathname === "/chat"}
          collapsed={collapsed}
          icon={
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M3 3h10v7H6l-3 3V3z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
            </svg>
          }
        />
        <NavItem
          href="/search"
          label="Search"
          active={pathname === "/search"}
          collapsed={collapsed}
          icon={
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle cx="7" cy="7" r="4" stroke="currentColor" strokeWidth="1.5" />
              <path d="M10 10l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          }
        />
        <NavItem
          href="/connectors"
          label="Connectors"
          active={pathname === "/connectors"}
          collapsed={collapsed}
          icon={
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M4 4h3v3H4V4zM9 4h3v3H9V4zM4 9h3v3H4V9z" stroke="currentColor" strokeWidth="1.5" />
              <path d="M11 10.5v-2h-2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          }
        />
        <NavItem
          href="/settings"
          label="Settings"
          active={pathname === "/settings"}
          collapsed={collapsed}
          icon={
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.5" />
              <path
                d="M8 2v2M8 12v2M2 8h2M12 8h2M3.8 3.8l1.4 1.4M10.8 10.8l1.4 1.4M3.8 12.2l1.4-1.4M10.8 5.2l1.4-1.4"
                stroke="currentColor"
                strokeWidth="1.2"
                strokeLinecap="round"
              />
            </svg>
          }
        />
      </nav>

      {/* Footer: theme toggle */}
      <div className="border-t border-b-secondary px-2 py-2">
        <button
          onClick={cycleTheme}
          className={`flex items-center gap-2 w-full rounded-[var(--radius-sm)] text-xs text-t-tertiary hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer ${
            collapsed ? "justify-center p-2" : "px-2.5 py-1.5"
          }`}
          title={`Theme: ${theme}`}
        >
          {themeIcon}
          {!collapsed && <span className="capitalize">{theme}</span>}
        </button>
      </div>
    </aside>
  );
}
