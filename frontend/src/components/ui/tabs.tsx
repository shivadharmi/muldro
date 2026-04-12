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
      className="flex gap-0.5 border-b border-b-secondary mb-6"
      role="tablist"
    >
      {tabs.map((tab) => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={active === tab.key}
          onClick={() => onChange(tab.key)}
          className={`px-4 py-2.5 text-[13px] font-medium transition-colors duration-150 border-b-2 -mb-px whitespace-nowrap cursor-pointer ${
            active === tab.key
              ? "border-j-primary text-j-primary"
              : "border-transparent text-t-muted hover:text-t-secondary"
          }`}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className="ml-1.5 text-[11px] text-t-muted">({tab.count})</span>
          )}
        </button>
      ))}
    </div>
  );
}
