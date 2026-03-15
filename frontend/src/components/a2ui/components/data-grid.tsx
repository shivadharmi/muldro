"use client";

import { useState } from "react";
import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UIDataGrid({ component }: Props) {
  const columns = (component.properties.columns as Array<{ key: string; label: string }>) || [];
  const rows = (component.properties.rows as Array<Record<string, unknown>>) || [];
  const pageSize = (component.properties.page_size as number) || 20;
  const [page, setPage] = useState(0);

  const totalPages = Math.ceil(rows.length / pageSize);
  const pagedRows = rows.slice(page * pageSize, (page + 1) * pageSize);

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-neutral-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-800 bg-neutral-900/50">
              {columns.map((col) => (
                <th key={col.key} className="px-3 py-2 text-left text-xs font-medium text-neutral-400">
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((row, i) => (
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
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-neutral-500">
          <span>Page {page + 1} of {totalPages}</span>
          <div className="flex gap-1">
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
              className="px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-30"
            >
              Prev
            </button>
            <button
              onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-30"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
