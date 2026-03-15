"use client";

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-neutral-800 mb-4">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={`px-3 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
            active === tab.key
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-neutral-500 hover:text-neutral-300"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
