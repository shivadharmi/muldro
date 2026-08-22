export const TRUST_LEVEL_COLORS: Record<string, string> = {
  first_use: "bg-t-muted",
  learning: "bg-j-info",
  trusted: "bg-j-success",
  autonomous: "bg-j-secondary",
  blocked: "bg-j-error",
};

export const TRUST_LEVEL_LABELS: Record<string, string> = {
  first_use: "First Use",
  learning: "Learning",
  trusted: "Trusted",
  autonomous: "Autonomous",
  blocked: "Blocked",
};

export const CEILING_OPTIONS = [
  { value: "blocked", label: "Blocked" },
  { value: "first_use", label: "First Use" },
  { value: "learning", label: "Learning" },
  { value: "trusted", label: "Trusted" },
  { value: "autonomous", label: "Autonomous (no limit)" },
];
