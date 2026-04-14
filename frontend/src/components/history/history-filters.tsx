"use client";

import { useCallback, useState } from "react";
import { useHistoryStore } from "@/stores/history-store";

// ── Types ────────────────────────────────────────────────────────────────────

type StatusOption = "all" | "executing" | "completed" | "failed" | "awaiting_approval" | "cancelled";
type SourceOption = "all" | "background" | "user_message" | "schedule" | "event";

interface DatePreset {
  label: string;
  hours: number;
}

// ── Constants ────────────────────────────────────────────────────────────────

const STATUS_OPTIONS: { value: StatusOption; label: string }[] = [
  { value: "all", label: "All Status" },
  { value: "executing", label: "Executing" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "awaiting_approval", label: "Awaiting Approval" },
  { value: "cancelled", label: "Cancelled" },
];

const SOURCE_OPTIONS: { value: SourceOption; label: string }[] = [
  { value: "all", label: "All Sources" },
  { value: "background", label: "Background" },
  { value: "user_message", label: "User Message" },
  { value: "schedule", label: "Schedule" },
  { value: "event", label: "Event" },
];

const DATE_PRESETS: DatePreset[] = [
  { label: "Last 24h", hours: 24 },
  { label: "Last 7d", hours: 24 * 7 },
  { label: "Last 30d", hours: 24 * 30 },
];

// ── Component ────────────────────────────────────────────────────────────────

export function HistoryFilters() {
  const filters = useHistoryStore((s) => s.filters);
  const setFilters = useHistoryStore((s) => s.setFilters);

  // Track the active date preset. We store which preset is active in state
  // rather than computing it from Date.now() during render (impure call).
  const [activePresetHours, setActivePresetHours] = useState<number | null>(null);

  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setFilters({ search: e.target.value });
    },
    [setFilters],
  );

  const handleStatusChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      setFilters({ status: e.target.value });
    },
    [setFilters],
  );

  const handleSourceChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      setFilters({ source: e.target.value });
    },
    [setFilters],
  );

  const handleDatePreset = useCallback(
    (hours: number) => {
      const from = new Date(Date.now() - hours * 3600 * 1000).toISOString();
      setFilters({ dateFrom: from });
      setActivePresetHours(hours);
    },
    [setFilters],
  );

  const handleClearDate = useCallback(() => {
    setFilters({ dateFrom: null });
    setActivePresetHours(null);
  }, [setFilters]);

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-[#21262d]">
      {/* Search input */}
      <div className="relative flex-1 min-w-[200px] max-w-xs">
        <svg
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#484f58] pointer-events-none"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
        >
          <circle cx="11" cy="11" r="8" stroke="currentColor" strokeWidth="2" />
          <path d="m21 21-4.35-4.35" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        <input
          type="text"
          value={filters.search}
          onChange={handleSearchChange}
          placeholder="Search runs, plans, steps..."
          className="w-full bg-[#161b22] border border-[#21262d] rounded-md pl-8 pr-3 py-1.5 text-sm text-[#e6edf3] placeholder-[#484f58] focus:outline-none focus:border-[#58a6ff] transition-colors"
        />
      </div>

      {/* Status dropdown */}
      <select
        value={filters.status}
        onChange={handleStatusChange}
        className="bg-[#161b22] border border-[#21262d] rounded-md px-2.5 py-1.5 text-sm text-[#e6edf3] focus:outline-none focus:border-[#58a6ff] transition-colors cursor-pointer appearance-none pr-7"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none'%3E%3Cpath d='M6 9l6 6 6-6' stroke='%238b949e' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")`,
          backgroundRepeat: "no-repeat",
          backgroundPosition: "right 8px center",
        }}
      >
        {STATUS_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>

      {/* Source dropdown */}
      <select
        value={filters.source}
        onChange={handleSourceChange}
        className="bg-[#161b22] border border-[#21262d] rounded-md px-2.5 py-1.5 text-sm text-[#e6edf3] focus:outline-none focus:border-[#58a6ff] transition-colors cursor-pointer appearance-none pr-7"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none'%3E%3Cpath d='M6 9l6 6 6-6' stroke='%238b949e' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")`,
          backgroundRepeat: "no-repeat",
          backgroundPosition: "right 8px center",
        }}
      >
        {SOURCE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>

      {/* Date preset buttons */}
      <div className="flex items-center gap-1">
        {DATE_PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() => handleDatePreset(preset.hours)}
            className={`px-2.5 py-1.5 text-xs rounded-md border transition-colors cursor-pointer ${
              activePresetHours === preset.hours
                ? "bg-[#1f2d3d] border-[#58a6ff] text-[#58a6ff]"
                : "bg-[#161b22] border-[#21262d] text-[#8b949e] hover:text-[#e6edf3] hover:border-[#30363d]"
            }`}
          >
            {preset.label}
          </button>
        ))}
        {filters.dateFrom && (
          <button
            type="button"
            onClick={handleClearDate}
            className="px-2 py-1.5 text-xs rounded-md border border-[#21262d] bg-[#161b22] text-[#484f58] hover:text-[#8b949e] transition-colors cursor-pointer"
            title="Clear date filter"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
