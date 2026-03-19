"use client";

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: string; label: string; count?: number }[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div
      className="flex gap-1 border-b border-b-primary mb-4"
      role="tablist"
    >
      {tabs.map((tab) => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={active === tab.key}
          onClick={() => onChange(tab.key)}
          className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 -mb-px whitespace-nowrap cursor-pointer ${
            active === tab.key
              ? "border-j-primary text-j-primary"
              : "border-transparent text-t-tertiary hover:text-t-secondary"
          }`}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="ml-1.5 text-xs text-t-muted">({tab.count})</span>
          )}
        </button>
      ))}
    </div>
  );
}
