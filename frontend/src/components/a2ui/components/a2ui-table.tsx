"use client";

import { useState } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UITable({ component }: Props) {
  const columns = (component.properties.columns as Array<{ key: string; label: string }>) || [];
  const rows = (component.properties.rows as Array<Record<string, unknown>>) || [];
  const sortable = component.properties.sortable as boolean;
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortAsc, setSortAsc] = useState(true);

  const sortedRows = sortKey
    ? [...rows].sort((a, b) => {
        const av = String(a[sortKey] ?? "");
        const bv = String(b[sortKey] ?? "");
        return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      })
    : rows;

  const handleSort = (key: string) => {
    if (!sortable) return;
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  return (
    <div className="overflow-x-auto rounded-lg border border-neutral-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-neutral-800 bg-neutral-900/50">
            {columns.map((col) => (
              <th
                key={col.key}
                onClick={() => handleSort(col.key)}
                className={`px-3 py-2 text-left text-xs font-medium text-neutral-400 ${sortable ? "cursor-pointer hover:text-white" : ""}`}
              >
                {col.label}
                {sortKey === col.key && (
                  <span className="ml-1">{sortAsc ? "\u2191" : "\u2193"}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, i) => (
            <tr key={i} className="border-b border-neutral-800/50 hover:bg-neutral-800/30">
              {columns.map((col) => (
                <td key={col.key} className="px-3 py-2 text-neutral-300">
                  {String(row[col.key] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
