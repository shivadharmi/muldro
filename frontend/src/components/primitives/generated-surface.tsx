"use client";

import { MarkdownRenderer } from "@/components/jarvis/markdown-renderer";
import type {
  GeneratedSurface,
  SurfaceKind,
  ChecklistItem,
  PlanData,
  TableData,
  ComparisonData,
  ApprovalData,
  AlertData,
  BriefingData,
  TimelineEntry,
} from "@/lib/types/surfaces";

interface Props {
  surface: GeneratedSurface;
  onPin?: (id: string) => void;
  onRemove?: (id: string) => void;
}

export function GeneratedSurfaceCard({ surface, onPin, onRemove }: Props) {
  return (
    <div className="rounded-[var(--radius-md)] border border-b-primary bg-surface-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-surface-1 border-b border-b-primary">
        <div className="flex items-center gap-2">
          <SurfaceIcon kind={surface.kind} />
          <span className="text-sm font-medium text-t-primary">
            {surface.title}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {onPin && (
            <button
              onClick={() => onPin(surface.id)}
              className={`p-1 rounded text-xs cursor-pointer ${
                surface.pinned
                  ? "text-accent-primary"
                  : "text-t-tertiary hover:text-t-secondary"
              }`}
              aria-label={surface.pinned ? "Unpin" : "Pin"}
            >
              📌
            </button>
          )}
          {onRemove && (
            <button
              onClick={() => onRemove(surface.id)}
              className="p-1 rounded text-xs text-t-tertiary hover:text-t-secondary cursor-pointer"
              aria-label="Remove"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="p-3">
        <SurfaceBody surface={surface} />
      </div>
    </div>
  );
}

function SurfaceIcon({ kind }: { kind: SurfaceKind }) {
  const icons: Record<SurfaceKind, string> = {
    summary: "📋",
    briefing: "📰",
    plan: "🗺️",
    checklist: "☑️",
    approval: "🔐",
    comparison: "⚖️",
    alert: "⚠️",
    timeline: "📅",
    table: "📊",
    recommendation: "💡",
    activity: "⚡",
  };
  return <span className="text-sm">{icons[kind] ?? "📋"}</span>;
}

function SurfaceBody({ surface }: { surface: GeneratedSurface }) {
  const { kind, data } = surface;

  // Extract WorkspaceSurfaceMetadata fields (common across all push types)
  const meta = data as Record<string, unknown>;
  const responsePreview = (meta.response_preview ?? meta.text ?? "") as string;
  const reasoning = (meta.reasoning ?? "") as string;
  const decision = (meta.decision ?? "") as string;
  const priority = (meta.priority ?? "") as string;
  const highlights = Array.isArray(meta.highlights) ? meta.highlights as string[] : [];

  if (kind === "summary" || kind === "recommendation" || kind === "activity") {
    const text = responsePreview || reasoning;
    if (!text && highlights.length === 0) return <FallbackJson data={data} />;
    return (
      <div className="text-sm text-t-secondary">
        {text && <MarkdownRenderer content={text} />}
        {highlights.length > 0 && (
          <ul className="mt-2 space-y-1">
            {highlights.map((h, i) => (
              <li key={i} className="flex items-start gap-1.5">
                <span className="text-accent-primary mt-0.5">•</span>
                <span>{h}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  if (kind === "checklist" && Array.isArray(data.items)) {
    const items = data.items as ChecklistItem[];
    return (
      <ul className="space-y-1.5">
        {items.map((ci, i) => (
          <li key={i} className="flex items-center gap-2 text-sm">
            <span className={ci.checked ? "text-status-success" : "text-t-tertiary"}>
              {ci.checked ? "☑" : "☐"}
            </span>
            <span className={ci.checked ? "line-through text-t-tertiary" : "text-t-secondary"}>
              {ci.label}
            </span>
          </li>
        ))}
      </ul>
    );
  }

  if (kind === "plan") {
    // Full plan with tasks array (from plan detail fetch)
    if (Array.isArray(data.tasks)) {
      const d = data as unknown as PlanData;
      return (
        <div className="space-y-2">
          {d.goal && <p className="text-sm font-medium text-t-primary">{d.goal}</p>}
          <ol className="space-y-1.5 list-decimal list-inside">
            {d.tasks.map((t, i) => (
              <li key={i} className="text-sm text-t-secondary">
                <span className="font-medium">{t.task_type}</span>
                {t.input_data?.description && (
                  <span className="text-t-tertiary ml-1">— {t.input_data.description}</span>
                )}
              </li>
            ))}
          </ol>
        </div>
      );
    }
    // Workspace push: show response preview + decision badge
    return (
      <div className="space-y-2">
        {decision && (
          <div className="flex items-center gap-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-j-secondary-soft text-j-secondary border border-j-secondary/30">
              {decision}
            </span>
            {priority && priority !== "medium" && (
              <span className={`text-xs px-2 py-0.5 rounded-full ${
                priority === "high" || priority === "critical"
                  ? "bg-status-error/10 text-status-error"
                  : "bg-surface-2 text-t-tertiary"
              }`}>
                {priority}
              </span>
            )}
          </div>
        )}
        {responsePreview && <div className="text-sm text-t-secondary"><MarkdownRenderer content={responsePreview} /></div>}
        {!responsePreview && reasoning && <div className="text-sm text-t-tertiary"><MarkdownRenderer content={reasoning} /></div>}
      </div>
    );
  }

  if (kind === "table" && Array.isArray(data.columns) && Array.isArray(data.rows)) {
    const d = data as unknown as TableData;
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-b-primary">
              {d.columns.map((col) => (
                <th key={col.key} className="text-left py-1.5 px-2 text-xs font-medium text-t-tertiary uppercase">
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.rows.map((row, i) => (
              <tr key={i} className="border-b border-b-primary/50">
                {d.columns.map((col) => (
                  <td key={col.key} className="py-1.5 px-2 text-t-secondary">
                    {String(row[col.key] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (kind === "comparison" && Array.isArray(data.options) && Array.isArray(data.rows)) {
    const d = data as unknown as ComparisonData;
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-b-primary">
              <th className="text-left py-1.5 px-2 text-xs font-medium text-t-tertiary" />
              {d.options.map((opt) => (
                <th key={opt} className="text-left py-1.5 px-2 text-xs font-medium text-t-tertiary uppercase">
                  {opt}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.rows.map((row, i) => (
              <tr key={i} className="border-b border-b-primary/50">
                <td className="py-1.5 px-2 font-medium text-t-secondary">{row.label}</td>
                {row.values.map((v, j) => (
                  <td key={j} className="py-1.5 px-2 text-t-secondary">{v}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (kind === "approval") {
    const d = data as unknown as ApprovalData;
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${
            d.risk_level === "high" ? "bg-status-error/10 text-status-error"
              : d.risk_level === "medium" ? "bg-status-warning/10 text-status-warning"
              : "bg-status-success/10 text-status-success"
          }`}>
            {d.risk_level ?? "medium"} risk
          </span>
        </div>
        {d.summary && <div className="text-sm text-t-secondary"><MarkdownRenderer content={d.summary} /></div>}
        {d.impact && <p className="text-xs text-t-tertiary">Impact: {d.impact}</p>}
        {d.reversibility && (
          <p className="text-xs text-t-tertiary">Reversibility: {d.reversibility}</p>
        )}
      </div>
    );
  }

  if (kind === "alert") {
    const d = data as unknown as AlertData;
    const level = d.level ?? "info";
    const levelColors: Record<string, string> = {
      info: "border-accent-primary bg-accent-primary/5",
      warning: "border-status-warning bg-status-warning/5",
      error: "border-status-error bg-status-error/5",
      success: "border-status-success bg-status-success/5",
    };
    return (
      <div className={`border-l-4 p-3 rounded-r-[var(--radius-sm)] ${levelColors[level] || levelColors.info}`}>
        <p className="text-sm font-medium text-t-primary">{d.title ?? "Alert"}</p>
        {d.message && <div className="text-sm text-t-secondary mt-1"><MarkdownRenderer content={d.message} /></div>}
      </div>
    );
  }

  if (kind === "briefing") {
    const d = data as unknown as BriefingData;
    return (
      <div className="space-y-2">
        {d.headline && <p className="text-sm font-medium text-t-primary">{d.headline}</p>}
        {d.full_text && <div className="text-sm text-t-secondary"><MarkdownRenderer content={d.full_text} /></div>}
        {d.sections?.map((s, i) => (
          <div key={i}>
            <p className="text-xs font-medium text-t-secondary uppercase tracking-wider">{s.title}</p>
            <p className="text-sm text-t-secondary">{s.content}</p>
          </div>
        ))}
      </div>
    );
  }

  if (kind === "timeline" && Array.isArray(data.entries)) {
    const entries = data.entries as TimelineEntry[];
    return (
      <div className="space-y-3">
        {entries.map((entry, i) => (
          <div key={i} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span className="w-2 h-2 rounded-full bg-accent-primary mt-1.5" />
              {i < entries.length - 1 && (
                <span className="w-px flex-1 bg-surface-2 mt-1" />
              )}
            </div>
            <div className="flex-1 pb-3">
              <p className="text-sm text-t-secondary">{entry.label}</p>
              <p className="text-xs text-t-tertiary">{entry.timestamp}</p>
              {entry.detail && (
                <p className="text-xs text-t-tertiary mt-0.5">{entry.detail}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    );
  }

  // Generic fallback: show response_preview/reasoning if available
  if (responsePreview || reasoning) {
    return (
      <div className="space-y-2">
        {decision && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-surface-2 text-t-tertiary">
            {decision}
          </span>
        )}
        {responsePreview && <div className="text-sm text-t-secondary"><MarkdownRenderer content={responsePreview} /></div>}
        {!responsePreview && reasoning && <div className="text-sm text-t-tertiary"><MarkdownRenderer content={reasoning} /></div>}
      </div>
    );
  }

  return <FallbackJson data={data} />;
}

function FallbackJson({ data }: { data: Record<string, unknown> }) {
  return (
    <pre className="text-xs text-t-tertiary overflow-x-auto">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
