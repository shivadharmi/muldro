"use client";

import { useState } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

// A row is `{ cells: string[] }`, positionally aligned to `columns`. The legacy keyed shape is
// still accepted so a surface persisted before this change still renders.
function cellFor(row: unknown, column: { key: string }, index: number): string {
  if (row && typeof row === "object" && "cells" in row) {
    const cells = (row as { cells?: unknown[] }).cells;
    return Array.isArray(cells) ? String(cells[index] ?? "") : "";
  }
  const keyed = row as Record<string, unknown> | null;
  return keyed ? String(keyed[column.key] ?? "") : "";
}

export function A2UITable({ component }: Props) {
  const columns = (component.properties.columns as Array<{ key: string; label: string }>) || [];
  const rows = (component.properties.rows as unknown[]) || [];
  const sortable = component.properties.sortable as boolean;
  const [sortIndex, setSortIndex] = useState<number | null>(null);
  const [sortAsc, setSortAsc] = useState(true);

  const sortedRows =
    sortIndex !== null && columns[sortIndex]
      ? [...rows].sort((a, b) => {
          const av = cellFor(a, columns[sortIndex], sortIndex);
          const bv = cellFor(b, columns[sortIndex], sortIndex);
          return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
        })
      : rows;

  const handleSort = (index: number) => {
    if (!sortable) return;
    if (sortIndex === index) {
      setSortAsc(!sortAsc);
    } else {
      setSortIndex(index);
      setSortAsc(true);
    }
  };

  return (
    <div className="overflow-x-auto rounded-[var(--radius-lg)] border border-b-primary">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-b-primary bg-surface-1">
            {columns.map((col, ci) => (
              <th
                key={col.key}
                onClick={() => handleSort(ci)}
                className={`px-3 py-2 text-left text-xs font-medium text-t-secondary ${sortable ? "cursor-pointer hover:text-t-primary" : ""}`}
              >
                {col.label}
                {sortIndex === ci && (
                  <span className="ml-1">{sortAsc ? "\u2191" : "\u2193"}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, i) => (
            <tr key={i} className="border-b border-b-primary/50 hover:bg-surface-2">
              {columns.map((col, ci) => (
                <td key={col.key} className="px-3 py-2 text-t-primary">
                  {cellFor(row, col, ci)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
