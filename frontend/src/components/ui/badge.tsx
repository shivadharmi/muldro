const VARIANTS: Record<string, string> = {
  default: "bg-neutral-700 text-neutral-300",
  blue: "bg-blue-900/50 text-blue-400 border border-blue-800",
  green: "bg-green-900/50 text-green-400 border border-green-800",
  yellow: "bg-yellow-900/50 text-yellow-400 border border-yellow-800",
  red: "bg-red-900/50 text-red-400 border border-red-800",
  purple: "bg-purple-900/50 text-purple-400 border border-purple-800",
};

export function Badge({
  children,
  variant = "default",
  className = "",
}: {
  children: React.ReactNode;
  variant?: keyof typeof VARIANTS;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${VARIANTS[variant] || VARIANTS.default} ${className}`}
    >
      {children}
    </span>
  );
}

export function statusVariant(status: string): keyof typeof VARIANTS {
  switch (status) {
    case "pending":
    case "created":
    case "detecting":
      return "yellow";
    case "approved":
    case "completed":
    case "ok":
    case "normal":
      return "green";
    case "rejected":
    case "failed":
    case "paused":
      return "red";
    case "executing":
    case "in_progress":
    case "degraded":
      return "blue";
    default:
      return "default";
  }
}

export function priorityVariant(priority: string): keyof typeof VARIANTS {
  switch (priority) {
    case "high":
    case "critical":
      return "red";
    case "medium":
      return "yellow";
    case "low":
      return "green";
    default:
      return "default";
  }
}

export function riskVariant(risk: string): keyof typeof VARIANTS {
  switch (risk) {
    case "high":
    case "critical":
      return "red";
    case "medium":
      return "yellow";
    case "low":
      return "green";
    default:
      return "default";
  }
}
