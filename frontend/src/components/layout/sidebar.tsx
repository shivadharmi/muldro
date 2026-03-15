"use client";

import { usePathname } from "next/navigation";
import { NavItem } from "./nav-item";
import { useQuery } from "@tanstack/react-query";
import { fetchSystemDashboard } from "@/lib/api";

export function Sidebar() {
  const pathname = usePathname();
  const { data } = useQuery({
    queryKey: ["system-dashboard-nav"],
    queryFn: fetchSystemDashboard,
    refetchInterval: 30_000,
  });

  const pendingApprovals = data?.queues?.approvals_pending ?? 0;

  return (
    <aside className="w-56 flex-shrink-0 border-r border-neutral-800 bg-neutral-950 flex flex-col h-screen overflow-y-auto">
      <div className="px-4 py-4 border-b border-neutral-800">
        <h1 className="text-lg font-semibold tracking-tight">Jarvis</h1>
        <p className="text-[10px] text-neutral-600 mt-0.5">AI Operating System</p>
      </div>

      <nav className="flex-1 px-2 py-3 space-y-5">
        <div>
          <p className="px-2 text-[10px] font-semibold text-neutral-600 uppercase tracking-wider mb-1">
            Overview
          </p>
          <NavItem
            href="/"
            label="Dashboard"
            active={pathname === "/"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5"/><rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5"/><rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5"/><rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.5"/></svg>}
          />
        </div>

        <div>
          <p className="px-2 text-[10px] font-semibold text-neutral-600 uppercase tracking-wider mb-1">
            Intelligence
          </p>
          <NavItem
            href="/chat"
            label="Chat"
            active={pathname === "/chat"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 3h10v7H6l-3 3V3z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/></svg>}
          />
          <NavItem
            href="/briefings"
            label="Briefings"
            active={pathname === "/briefings"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 2h8v12H4V2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/><path d="M6 5h4M6 7.5h4M6 10h2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>}
          />
          <NavItem
            href="/search"
            label="Search"
            active={pathname === "/search"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="4" stroke="currentColor" strokeWidth="1.5"/><path d="M10 10l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>}
          />
        </div>

        <div>
          <p className="px-2 text-[10px] font-semibold text-neutral-600 uppercase tracking-wider mb-1">
            Operations
          </p>
          <NavItem
            href="/approvals"
            label="Approvals"
            active={pathname === "/approvals"}
            badge={pendingApprovals > 0 ? pendingApprovals : undefined}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 8.5l3 3 7-7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
          />
          <NavItem
            href="/tasks"
            label="Tasks"
            active={pathname.startsWith("/tasks")}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 4h10M3 8h10M3 12h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>}
          />
          <NavItem
            href="/schedules"
            label="Schedules"
            active={pathname === "/schedules"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="1.5"/><path d="M8 5v3l2 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
          />
        </div>

        <div>
          <p className="px-2 text-[10px] font-semibold text-neutral-600 uppercase tracking-wider mb-1">
            Data
          </p>
          <NavItem
            href="/connectors"
            label="Connectors"
            active={pathname === "/connectors"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4h3v3H4V4zM9 4h3v3H9V4zM4 9h3v3H4V9z" stroke="currentColor" strokeWidth="1.5"/><path d="M11 10.5v-2h-2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>}
          />
          <NavItem
            href="/entities"
            label="Entities"
            active={pathname.startsWith("/entities")}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="4" r="2" stroke="currentColor" strokeWidth="1.5"/><circle cx="4" cy="12" r="2" stroke="currentColor" strokeWidth="1.5"/><circle cx="12" cy="12" r="2" stroke="currentColor" strokeWidth="1.5"/><path d="M8 6v2M6 10l-1.5 1M10 10l1.5 1" stroke="currentColor" strokeWidth="1.2"/></svg>}
          />
          <NavItem
            href="/memories"
            label="Memories"
            active={pathname === "/memories"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 3h8a1 1 0 011 1v8a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1z" stroke="currentColor" strokeWidth="1.5"/><path d="M6 6h4M6 8.5h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>}
          />
        </div>

        <div>
          <p className="px-2 text-[10px] font-semibold text-neutral-600 uppercase tracking-wider mb-1">
            Automation
          </p>
          <NavItem
            href="/executions"
            label="Executions"
            active={pathname.startsWith("/executions")}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 3v10l3-2 3 2 3-2V3L10 5 7 3 4 5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/></svg>}
          />
          <NavItem
            href="/triggers"
            label="Triggers"
            active={pathname === "/triggers"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M9 2L5 9h3l-1 5 5-7H9l1-5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/></svg>}
          />
        </div>

        <div>
          <p className="px-2 text-[10px] font-semibold text-neutral-600 uppercase tracking-wider mb-1">
            System
          </p>
          <NavItem
            href="/system"
            label="Health"
            active={pathname === "/system"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 8h3l1.5-4 3 8L11 8h3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
          />
          <NavItem
            href="/settings"
            label="Settings"
            active={pathname === "/settings"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.5"/><path d="M8 2v2M8 12v2M2 8h2M12 8h2M3.8 3.8l1.4 1.4M10.8 10.8l1.4 1.4M3.8 12.2l1.4-1.4M10.8 5.2l1.4-1.4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>}
          />
        </div>
      </nav>
    </aside>
  );
}
