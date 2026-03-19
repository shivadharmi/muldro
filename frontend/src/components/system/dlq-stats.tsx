"use client";

import type { DLQStats } from "@/lib/types";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Table, TableHeader, TableBody, Th, Td } from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";

export function DLQStatsView({ stats }: { stats: DLQStats | undefined }) {
  if (!stats) return <EmptyState title="Loading DLQ stats..." />;

  return (
    <div className="space-y-4">
      <Card>
        <CardBody>
          <p className="text-xs text-t-tertiary">Total DLQ Entries</p>
          <p className="text-2xl font-semibold">{stats.total}</p>
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <span className="text-sm font-medium">By Status</span>
          </CardHeader>
          {Object.keys(stats.by_status).length === 0 ? (
            <CardBody>
              <p className="text-xs text-t-muted">No entries</p>
            </CardBody>
          ) : (
            <Table>
              <TableHeader>
                <Th>Status</Th>
                <Th>Count</Th>
              </TableHeader>
              <TableBody>
                {Object.entries(stats.by_status).map(([status, count]) => (
                  <tr key={status}>
                    <Td className="text-t-primary">{status}</Td>
                    <Td>{count}</Td>
                  </tr>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>

        <Card>
          <CardHeader>
            <span className="text-sm font-medium">By Operation</span>
          </CardHeader>
          {Object.keys(stats.by_operation).length === 0 ? (
            <CardBody>
              <p className="text-xs text-t-muted">No entries</p>
            </CardBody>
          ) : (
            <Table>
              <TableHeader>
                <Th>Operation</Th>
                <Th>Count</Th>
              </TableHeader>
              <TableBody>
                {Object.entries(stats.by_operation).map(([op, count]) => (
                  <tr key={op}>
                    <Td className="text-t-primary">{op}</Td>
                    <Td>{count}</Td>
                  </tr>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}
