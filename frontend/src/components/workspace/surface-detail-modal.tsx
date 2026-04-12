"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { A2UIRenderer } from "@/components/a2ui/renderer";
import { A2UIExecutionSurface } from "@/components/a2ui/components/execution-surface";
import { handleA2UIAction } from "@/components/a2ui/action-handler";
import { fetchSurfaceDetail } from "@/lib/api";
import { useWsActionStore } from "@/stores/ws-action-store";
import type { WorkspaceSurface } from "@/stores/surface-store";
import type { DetailTabResponse, DetailTab } from "@/lib/a2ui-types";

interface Props {
  surface: WorkspaceSurface;
  open: boolean;
  onClose: () => void;
}

const priorityBadge: Record<string, string> = {
  low: "bg-gray-500/20 text-gray-400",
  medium: "bg-blue-500/20 text-blue-400",
  high: "bg-amber-500/20 text-amber-400",
  critical: "bg-red-500/20 text-red-400",
};

export function SurfaceDetailModal({ surface, open, onClose }: Props) {
  const sendAction = useWsActionStore((s) => s.sendAction);
  const tabs = surface.detail_config?.tabs ?? [];
  const defaultTabId = surface.detail_config?.default_tab ?? tabs[0]?.id ?? null;

  const [activeTabId, setActiveTabId] = useState<string | null>(defaultTabId);
  const [tabCache, setTabCache] = useState<Record<string, DetailTabResponse>>({});
  const [error, setError] = useState<string | null>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  // Render-phase reset when surface changes (React-recommended alternative to
  // setState-in-effect for "reset when prop changes" patterns).
  const [prevSurfaceId, setPrevSurfaceId] = useState(surface.id);
  if (prevSurfaceId !== surface.id) {
    setPrevSurfaceId(surface.id);
    setActiveTabId(defaultTabId);
    setTabCache({});
    setError(null);
  }

  // Derive loading from state: we are loading when the active tab has no
  // cached data and no error.  Eliminates synchronous setLoading calls in effects.
  const loading = !!activeTabId && open && !tabCache[activeTabId] && !error;

  // Fetch tab data on tab change — only setState inside async callbacks (allowed).
  useEffect(() => {
    if (!activeTabId || !open) return;
    if (tabCache[activeTabId]) return;
    if (error) return;

    let cancelled = false;

    fetchSurfaceDetail(surface.id, activeTabId)
      .then((data) => {
        if (!cancelled) {
          setTabCache((prev) => ({ ...prev, [activeTabId]: data }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Failed to load tab";
          setError(msg);
        }
      });

    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId, surface.id, open, tabCache]);

  // Close on backdrop click
  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === backdropRef.current) onClose();
    },
    [onClose]
  );

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  const activeTab = tabs.find((t: DetailTab) => t.id === activeTabId);
  const activeData = activeTabId ? tabCache[activeTabId] : null;

  return (
    <div
      ref={backdropRef}
      onClick={handleBackdropClick}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
    >
      <div className="bg-surface-0 border border-b-primary rounded-xl w-[95vw] max-w-[1200px] max-h-[90vh] flex flex-col overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-b-primary">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="text-base font-semibold text-t-primary truncate">
              {surface.preview.title}
            </h2>
            {surface.preview.priority && (
              <span
                className={`text-[10px] px-2 py-0.5 rounded font-medium shrink-0 ${priorityBadge[surface.preview.priority] ?? ""}`}
              >
                {surface.preview.priority}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-surface-2 transition-colors text-t-tertiary"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path
                d="M18 6L6 18M6 6l12 12"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {/* Tab bar */}
        {tabs.length > 0 && (
          <div className="flex border-b border-b-primary px-6">
            {tabs.map((tab: DetailTab) => (
              <button
                key={tab.id}
                onClick={() => { setActiveTabId(tab.id); if (error) setError(null); }}
                className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  activeTabId === tab.id
                    ? "border-accent-primary text-accent-primary"
                    : "border-transparent text-t-tertiary hover:text-t-secondary"
                }`}
              >
                {tab.label}
                {tab.badge_count != null && tab.badge_count > 0 && (
                  <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full bg-accent-primary/20 text-accent-primary">
                    {tab.badge_count}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading && (
            <div className="flex items-center justify-center py-8">
              <div className="w-5 h-5 border-2 border-accent-primary/30 border-t-accent-primary rounded-full animate-spin" />
              <span className="ml-2 text-sm text-t-tertiary">Loading {activeTab?.label}...</span>
            </div>
          )}

          {error && !loading && (
            <div className="rounded-lg bg-red-500/10 border border-red-500/20 p-4">
              <p className="text-sm text-red-400">{error}</p>
            </div>
          )}

          {activeData && !loading && (
            <div className="space-y-3">
              {activeData.sections.map((section) => (
                <CollapsibleSection
                  key={section.id}
                  title={section.title}
                  defaultCollapsed={section.collapsed}
                >
                  <A2UIRenderer
                    surface={{
                      type: "surface",
                      id: `detail-${surface.id}-${section.id}`,
                      children: section.children,
                      metadata: {},
                    }}
                    onAction={(action, payload) =>
                      handleA2UIAction(sendAction, action, {
                        ...payload,
                        surface_id: surface.id,
                      })
                    }
                  />
                </CollapsibleSection>
              ))}
            </div>
          )}

          {/* Live execution surface */}
          {surface.phase && (
            <A2UIExecutionSurface
              component={{
                type: "ExecutionSurface",
                id: `exec-${surface.id}`,
                properties: {
                  goal: surface.preview.title,
                  phase: surface.phase,
                  steps: surface.steps ?? [],
                  current_step: surface.current_step ?? null,
                  progress: surface.progress ?? "",
                  approval: surface.approval ?? null,
                  results: surface.results ?? null,
                },
                children: [],
                actions: [],
              }}
            />
          )}

          {!loading && !error && !activeData && tabs.length === 0 && (
            <p className="text-sm text-t-tertiary text-center py-8">
              No detail tabs available for this surface.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Collapsible Section ────────────────────────────────────────

interface SectionProps {
  title: string;
  defaultCollapsed: boolean;
  children: React.ReactNode;
}

function CollapsibleSection({ title, defaultCollapsed, children }: SectionProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  return (
    <div>
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center justify-between py-2 text-xs font-semibold text-t-secondary uppercase tracking-wide hover:text-t-primary transition-colors"
      >
        {title}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          className={`text-t-tertiary transition-transform ${collapsed ? "" : "rotate-180"}`}
        >
          <path
            d="M6 9l6 6 6-6"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {!collapsed && <div className="space-y-2 pb-2">{children}</div>}
    </div>
  );
}
