"use client";

// ── Bar Chart ─────────────────────────────────────────────────────
// CSS-based vertical bar chart with value labels and category names.

interface BarChartProps {
  data: { label: string; value: number; color?: string }[];
  height?: number; // default 120
}

export function BarChart({ data, height = 120 }: BarChartProps) {
  const maxValue = Math.max(...data.map((d) => d.value), 1);

  return (
    <div
      className="flex items-end gap-2"
      style={{ height: `${height}px` }}
    >
      {data.map((item) => {
        const pct = (item.value / maxValue) * 100;
        const barColor = item.color ?? "var(--muldro-chart-1)";

        return (
          <div
            key={item.label}
            className="flex-1 flex flex-col items-center justify-end gap-1 h-full min-w-0"
          >
            <span className="text-[10px] text-t-tertiary font-semibold">
              {item.value}
            </span>
            <div
              className="w-full rounded-t"
              style={{
                height: `${pct}%`,
                backgroundColor: barColor,
                minHeight: item.value > 0 ? "2px" : "0px",
              }}
            />
            <span className="text-[10px] text-t-muted truncate w-full text-center">
              {item.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
