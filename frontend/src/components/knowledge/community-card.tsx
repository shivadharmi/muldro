"use client";

// ── Community Card ────────────────────────────────────────────────
// Small card representing a detected community around a seed entity.

function resolveCssVar(varExpr: string): string {
  if (typeof window === "undefined") return "#888";
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(varExpr.replace("var(", "").replace(")", ""))
      .trim() || "#888"
  );
}

const SEED_TYPE_COLORS: Record<string, string> = {
  person: "var(--jarvis-primary)",
  organization: "var(--jarvis-secondary)",
  project: "var(--jarvis-accent)",
  document: "var(--jarvis-warning)",
  repository: "var(--jarvis-error)",
};

const DEFAULT_SEED_COLOR = "var(--jarvis-text-muted)";

function getSeedColor(type: string): string {
  return resolveCssVar(SEED_TYPE_COLORS[type.toLowerCase()] ?? DEFAULT_SEED_COLOR);
}

interface CommunityCardProps {
  name: string;
  memberCount: number;
  seedType: string;
  onClick?: () => void;
}

export function CommunityCard({
  name,
  memberCount,
  seedType,
  onClick,
}: CommunityCardProps) {
  const badgeColor = getSeedColor(seedType);

  return (
    <button
      type="button"
      onClick={onClick}
      className="bg-surface-2 border border-b-secondary rounded-[var(--radius-sm)] p-3 cursor-pointer hover:border-j-primary transition-colors text-left w-full"
    >
      <p className="text-sm font-semibold text-t-primary truncate">{name}</p>
      <p className="text-xs text-t-muted mt-0.5">
        {memberCount} {memberCount === 1 ? "member" : "members"}
      </p>
      <span
        className="inline-block mt-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium"
        style={{
          color: badgeColor,
          backgroundColor: `color-mix(in srgb, ${badgeColor} 15%, transparent)`,
          border: `1px solid color-mix(in srgb, ${badgeColor} 30%, transparent)`,
        }}
      >
        {seedType}
      </span>
    </button>
  );
}
