"use client";

import { usePathname } from "next/navigation";
import { NavItem } from "./nav-item";
import { useTheme } from "@/lib/theme";
import { useAuth } from "@/lib/auth";

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

function JarvisMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 56 56"
      fill="none"
      aria-hidden="true"
      className="flex-shrink-0"
    >
      <defs>
        <linearGradient id="jv-mark" x1="0" y1="0" x2="56" y2="56" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--jarvis-primary)" />
          <stop offset="1" stopColor="var(--jarvis-secondary)" />
        </linearGradient>
      </defs>
      <circle cx="28" cy="28" r="18" stroke="url(#jv-mark)" strokeWidth="1.5" opacity="0.35" />
      <circle cx="28" cy="28" r="11" stroke="url(#jv-mark)" strokeWidth="1.5" opacity="0.7" />
      <circle cx="28" cy="28" r="4" fill="url(#jv-mark)" />
    </svg>
  );
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();
  const { theme, setTheme, resolved } = useTheme();
  const { user } = useAuth();

  const displayName = user?.display_name?.trim() || user?.email?.split("@")[0] || "Account";
  const email = user?.email ?? "";
  const initial = displayName.charAt(0).toUpperCase() || "?";

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
      className={`flex-shrink-0 border-r border-b-secondary bg-surface-1 flex flex-col h-screen transition-[width] duration-200 ease-out ${
        collapsed ? "w-[60px]" : "w-[232px]"
      }`}
    >
      {/* Header with brand */}
      <div className={`h-12 flex items-center gap-2.5 border-b border-b-secondary ${collapsed ? "justify-center px-2" : "px-3.5"}`}>
        <button
          onClick={onToggle}
          className="p-1.5 rounded-[var(--radius-md)] hover:bg-surface-2 text-t-muted hover:text-t-primary transition-colors cursor-pointer"
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
        <JarvisMark />
        {!collapsed && (
          <div className="animate-fade-in flex items-baseline gap-1.5">
            <h1 className="text-[15px] font-semibold tracking-tight brand-gradient-text leading-none">
              Jarvis
            </h1>
            <span className="text-[10px] text-t-muted font-medium">OS</span>
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
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <rect x="2" y="2" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
              <rect x="10" y="2" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
              <rect x="2" y="10" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
              <rect x="10" y="10" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
            </svg>
          }
        />
        <NavItem
          href="/chat"
          label="Chat"
          active={pathname === "/chat"}
          collapsed={collapsed}
          icon={
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M3.5 3.5h11v8H7l-3.5 3.5V3.5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
            </svg>
          }
        />
        <NavItem
          href="/history"
          label="History"
          active={pathname === "/history"}
          collapsed={collapsed}
          icon={
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <circle cx="9" cy="9" r="6.5" stroke="currentColor" strokeWidth="1.4" />
              <path d="M9 5.5V9l2.5 2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          }
        />
        <NavItem
          href="/search"
          label="Search"
          active={pathname === "/search"}
          collapsed={collapsed}
          icon={
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <circle cx="8" cy="8" r="4.5" stroke="currentColor" strokeWidth="1.4" />
              <path d="M11.5 11.5l3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
          }
        />
        <NavItem
          href="/knowledge"
          label="Knowledge"
          active={pathname === "/knowledge"}
          collapsed={collapsed}
          icon={
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <circle cx="5.5" cy="5.5" r="2.5" stroke="currentColor" strokeWidth="1.3" />
              <circle cx="12.5" cy="5.5" r="2.5" stroke="currentColor" strokeWidth="1.3" />
              <circle cx="9" cy="12.5" r="2.5" stroke="currentColor" strokeWidth="1.3" />
              <path d="M7.2 7.5L8.2 10.5M10.8 7.5L9.8 10.5M7.5 5.5h3" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
            </svg>
          }
        />
        <NavItem
          href="/integrations"
          label="Integrations"
          active={pathname === "/integrations"}
          collapsed={collapsed}
          icon={
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M4 4h4v4H4V4zM10 4h4v4h-4V4zM4 10h4v4H4v-4z" stroke="currentColor" strokeWidth="1.4" rx="0.5" />
              <path d="M12.5 12v-2.5H10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
          }
        />

        <div className="pt-2 mt-2 border-t border-b-secondary">
          <NavItem
            href="/settings"
            label="Settings"
            active={pathname === "/settings"}
            collapsed={collapsed}
            icon={
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                <circle cx="9" cy="9" r="2.5" stroke="currentColor" strokeWidth="1.4" />
                <path
                  d="M9 2.5v2M9 13.5v2M2.5 9h2M13.5 9h2M4.4 4.4l1.4 1.4M12.2 12.2l1.4 1.4M4.4 13.6l1.4-1.4M12.2 5.8l1.4-1.4"
                  stroke="currentColor"
                  strokeWidth="1.2"
                  strokeLinecap="round"
                />
              </svg>
            }
          />
        </div>
      </nav>

      {/* Footer: user tile + compact theme toggle */}
      <div className="border-t border-b-secondary px-2 py-2 space-y-1">
        {collapsed ? (
          <>
            <div
              className="mx-auto w-8 h-8 rounded-full bg-j-primary-soft text-j-primary flex items-center justify-center text-[13px] font-semibold select-none"
              title={email ? `${displayName} · ${email}` : displayName}
            >
              {initial}
            </div>
            <button
              onClick={cycleTheme}
              className="flex items-center justify-center w-full rounded-[var(--radius-md)] p-2.5 text-t-muted hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer"
              title={`Theme: ${theme}`}
              aria-label={`Theme: ${theme}`}
            >
              {themeIcon}
            </button>
          </>
        ) : (
          <div className="flex items-center gap-2 px-2 py-1.5 rounded-[var(--radius-md)]">
            <div className="w-8 h-8 rounded-full bg-j-primary-soft text-j-primary flex items-center justify-center text-[13px] font-semibold select-none flex-shrink-0">
              {initial}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-[13px] text-t-primary font-medium truncate leading-tight">
                {displayName}
              </div>
              {email && (
                <div className="text-[11px] text-t-muted truncate leading-tight">{email}</div>
              )}
            </div>
            <button
              onClick={cycleTheme}
              className="flex-shrink-0 flex items-center justify-center p-1.5 rounded-[var(--radius-md)] text-t-muted hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer"
              title={`Theme: ${theme}`}
              aria-label={`Theme: ${theme}`}
            >
              {themeIcon}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
