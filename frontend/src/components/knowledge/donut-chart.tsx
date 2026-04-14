"use client";

// ── Donut Chart ───────────────────────────────────────────────────
// SVG-based donut using stroke-dasharray technique.
// viewBox 0 0 42 42 with radius ~15.915 gives circumference ~100
// so percentages map directly to dasharray values.

interface DonutChartProps {
  data: { label: string; value: number; color: string }[];
  total: number;
  size?: number; // default 130
}

const RADIUS = 15.915;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS; // ~100

export function DonutChart({ data, total, size = 130 }: DonutChartProps) {
  // Build segments with cumulative offset
  const segments: {
    label: string;
    value: number;
    color: string;
    percent: number;
    offset: number;
  }[] = [];

  let cumulativeOffset = 25; // start at 12 o'clock position

  for (const item of data) {
    const percent = total > 0 ? (item.value / total) * 100 : 0;
    segments.push({
      label: item.label,
      value: item.value,
      color: item.color,
      percent,
      offset: cumulativeOffset,
    });
    cumulativeOffset += percent;
  }

  return (
    <div className="flex flex-col items-center gap-3">
      {/* SVG donut */}
      <svg
        width={size}
        height={size}
        viewBox="0 0 42 42"
        className="block"
      >
        {/* Background ring */}
        <circle
          cx="21"
          cy="21"
          r={RADIUS}
          fill="none"
          stroke="var(--color-surface-3)"
          strokeWidth="3"
        />

        {/* Data segments */}
        {segments.map((seg) =>
          seg.percent > 0 ? (
            <circle
              key={seg.label}
              cx="21"
              cy="21"
              r={RADIUS}
              fill="none"
              stroke={seg.color}
              strokeWidth="3"
              strokeDasharray={`${seg.percent} ${CIRCUMFERENCE - seg.percent}`}
              strokeDashoffset={-seg.offset}
              strokeLinecap="butt"
              style={{ transition: "stroke-dasharray 0.3s ease" }}
            />
          ) : null,
        )}

        {/* Center text */}
        <text
          x="21"
          y="19.5"
          textAnchor="middle"
          className="fill-t-primary"
          style={{ fontSize: "6px", fontWeight: 700 }}
        >
          {total}
        </text>
        <text
          x="21"
          y="24"
          textAnchor="middle"
          className="fill-t-muted"
          style={{ fontSize: "2.8px" }}
        >
          total
        </text>
      </svg>

      {/* Legend */}
      <div className="flex flex-col gap-1.5 w-full">
        {segments.map((seg) => (
          <div key={seg.label} className="flex items-center gap-2 text-xs">
            <span
              className="inline-block w-2.5 h-2.5 rounded-full shrink-0"
              style={{ backgroundColor: seg.color }}
            />
            <span className="text-t-secondary truncate flex-1">
              {seg.label}
            </span>
            <span className="text-t-muted tabular-nums">{seg.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
