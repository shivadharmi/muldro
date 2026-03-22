"use client";

import { useState, useCallback, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { NavItem } from "./nav-item";
import { useQuery } from "@tanstack/react-query";
import { fetchSystemDashboard, fetchNotifications } from "@/lib/api";
import { useTheme } from "@/lib/theme";

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

function NavSection({
  id,
  title,
  icon,
  children,
  collapsed,
  expanded,
  onToggle,
  badge,
}: {
  id: string;
  title: string;
  icon: ReactNode;
  children: ReactNode;
  collapsed: boolean;
  expanded: boolean;
  onToggle: (id: string) => void;
  badge?: { count: number; color: "amber" | "red" | "cyan" } | null;
}) {
  if (collapsed) {
    return (
      <div className="space-y-0.5 py-1 relative">
        {children}
        {badge && badge.count > 0 && (
          <span
            className={`absolute -top-1 right-0 w-2 h-2 rounded-full ${
              badge.color === "red"
                ? "bg-j-error"
                : badge.color === "amber"
                  ? "bg-j-warning"
                  : "bg-j-primary"
            }`}
          />
        )}
      </div>
    );
  }

  return (
    <div>
      <button
        onClick={() => onToggle(id)}
        className="w-full flex items-center justify-between px-2.5 py-1.5 group cursor-pointer"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2">
          <span className="text-t-tertiary w-3.5 h-3.5">{icon}</span>
          <p className="text-[10px] font-semibold text-t-tertiary uppercase tracking-wider">
            {title}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {badge && badge.count > 0 && (
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                badge.color === "red"
                  ? "bg-j-error"
                  : badge.color === "amber"
                    ? "bg-j-warning"
                    : "bg-j-primary"
              }`}
            />
          )}
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            className={`text-t-muted group-hover:text-t-tertiary transition-transform duration-150 ${
              expanded ? "" : "-rotate-90"
            }`}
          >
            <path
              d="M3 4.5l3 3 3-3"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
      </button>
      <div
        className={`overflow-hidden transition-all duration-200 ${
          expanded ? "max-h-96 opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="space-y-0.5">{children}</div>
      </div>
    </div>
  );
}

const sectionIcon = (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
    <circle cx="3" cy="3" r="1" fill="currentColor" opacity="0.5" />
    <circle cx="7" cy="3" r="1" fill="currentColor" opacity="0.5" />
    <circle cx="3" cy="7" r="1" fill="currentColor" opacity="0.5" />
  </svg>
);

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();
  const { theme, setTheme, resolved } = useTheme();

  const [expandedSection, setExpandedSection] = useState<string | null>(() => {
    if (pathname === "/") return "home";
    if (["/chat", "/briefings"].some((p) => pathname.startsWith(p))) return "jarvis";
    if (
      ["/tasks", "/runs", "/goals", "/approvals", "/notifications"].some((p) =>
        pathname.startsWith(p)
      )
    )
      return "work";
    if (
      ["/connectors", "/integrations", "/entities", "/memories", "/search"].some((p) =>
        pathname.startsWith(p)
      )
    )
      return "data";
    return "system";
  });

  const toggleSection = useCallback((id: string) => {
    setExpandedSection((prev) => (prev === id ? null : id));
  }, []);

  const { data } = useQuery({
    queryKey: ["system-dashboard-nav"],
    queryFn: fetchSystemDashboard,
    refetchInterval: 30_000,
  });

  const { data: notifData } = useQuery({
    queryKey: ["notifications-nav"],
    queryFn: () => fetchNotifications(undefined, 50),
    refetchInterval: 30_000,
  });

  const pendingApprovals = data?.queues?.approvals_pending ?? 0;
  const unreadNotifications = Array.isArray(notifData)
    ? notifData.filter((n) => n.status === "sent" || n.status === "pending").length
    : 0;

  // Compute proactive signals from system dashboard
  const hasObservationError = data?.observations
    ? Object.values(data.observations).some((o) => o.status === "error")
    : false;
  const dlqPending = data?.queues?.dlq_pending ?? 0;
  const budgetMode = data?.budget?.budget_mode ?? "normal";
  const hasSystemIssue =
    dlqPending > 0 || budgetMode === "degraded" || budgetMode === "paused";

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

      {/* Navigation — 5 sections */}
      <nav className="flex-1 overflow-y-auto px-2 py-2 space-y-1">
        {/* Home */}
        <NavSection
          id="home"
          title="Home"
          icon={sectionIcon}
          collapsed={collapsed}
          expanded={expandedSection === "home"}
          onToggle={toggleSection}
        >
          <NavItem
            href="/"
            label="Dashboard"
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
        </NavSection>

        {/* Jarvis */}
        <NavSection
          id="jarvis"
          title="Jarvis"
          icon={sectionIcon}
          collapsed={collapsed}
          expanded={expandedSection === "jarvis"}
          onToggle={toggleSection}
        >
          <NavItem href="/chat" label="Chat" active={pathname === "/chat"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 3h10v7H6l-3 3V3z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/></svg>} />
          <NavItem href="/briefings" label="Briefings" active={pathname === "/briefings"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 2h8v12H4V2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/><path d="M6 5h4M6 7.5h4M6 10h2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>} />
        </NavSection>

        {/* Work */}
        <NavSection
          id="work"
          title="Work"
          icon={sectionIcon}
          collapsed={collapsed}
          expanded={expandedSection === "work"}
          onToggle={toggleSection}
        >
          <NavItem href="/tasks" label="Tasks" active={pathname.startsWith("/tasks")} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M3 8h10M3 12h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>} />
          <NavItem href="/runs" label="Runs" active={pathname.startsWith("/runs")} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M5 3l6 5-6 5V3z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/></svg>} />
          <NavItem href="/goals" label="Goals" active={pathname === "/goals"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5"/><circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.5"/><circle cx="8" cy="8" r="1" fill="currentColor"/></svg>} />
          <NavItem href="/approvals" label="Approvals" active={pathname === "/approvals"} badge={pendingApprovals > 0 ? pendingApprovals : undefined} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3 3 7-7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>} />
          <NavItem href="/notifications" label="Notifications" active={pathname === "/notifications"} badge={unreadNotifications > 0 ? unreadNotifications : undefined} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 6a4 4 0 018 0v3l1.5 2H2.5L4 9V6z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/><path d="M6 12a2 2 0 004 0" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>} />
        </NavSection>

        {/* Data */}
        <NavSection
          id="data"
          title="Data"
          icon={sectionIcon}
          collapsed={collapsed}
          expanded={expandedSection === "data"}
          onToggle={toggleSection}
          badge={hasObservationError ? { count: 1, color: "amber" } : null}
        >
          <NavItem href="/integrations" label="Integrations" active={pathname === "/integrations"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4h3v3H4V4zM9 4h3v3H9V4zM4 9h3v3H4V9z" stroke="currentColor" strokeWidth="1.5"/><path d="M11 10.5v-2h-2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>} />
          <NavItem href="/connectors" label="Connectors" active={pathname === "/connectors"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5" stroke="currentColor" strokeWidth="1.5"/><path d="M8 5v3l2 1" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>} />
          <NavItem href="/entities" label="Entities" active={pathname.startsWith("/entities")} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="4" r="2" stroke="currentColor" strokeWidth="1.5"/><circle cx="4" cy="12" r="2" stroke="currentColor" strokeWidth="1.5"/><circle cx="12" cy="12" r="2" stroke="currentColor" strokeWidth="1.5"/><path d="M8 6v2M6 10l-1.5 1M10 10l1.5 1" stroke="currentColor" strokeWidth="1.2"/></svg>} />
          <NavItem href="/memories" label="Memories" active={pathname === "/memories"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 3h8a1 1 0 011 1v8a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1z" stroke="currentColor" strokeWidth="1.5"/><path d="M6 6h4M6 8.5h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>} />
          <NavItem href="/search" label="Search" active={pathname === "/search"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="4" stroke="currentColor" strokeWidth="1.5"/><path d="M10 10l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>} />
        </NavSection>

        {/* System */}
        <NavSection
          id="system"
          title="System"
          icon={sectionIcon}
          collapsed={collapsed}
          expanded={expandedSection === "system"}
          onToggle={toggleSection}
          badge={hasSystemIssue ? { count: dlqPending, color: "red" } : null}
        >
          <NavItem href="/system" label="Health" active={pathname === "/system"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 8h3l1.5-4 3 8L11 8h3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>} />
          <NavItem href="/traces" label="Traces" active={pathname === "/traces"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 4h12M2 8h8M2 12h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><circle cx="13" cy="11" r="2" stroke="currentColor" strokeWidth="1.5"/></svg>} />
          <NavItem href="/agents" label="Agents" active={pathname === "/agents"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="5" r="3" stroke="currentColor" strokeWidth="1.5"/><path d="M3 14c0-2.8 2.2-5 5-5s5 2.2 5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>} />
          <NavItem href="/settings" label="Settings" active={pathname === "/settings"} collapsed={collapsed} icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.5"/><path d="M8 2v2M8 12v2M2 8h2M12 8h2M3.8 3.8l1.4 1.4M10.8 10.8l1.4 1.4M3.8 12.2l1.4-1.4M10.8 5.2l1.4-1.4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>} />
        </NavSection>
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
