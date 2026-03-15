"use client";

import type { ObservationSourceInfo } from "@/lib/types";
import { Card, CardHeader } from "@/components/ui/card";
import { Table, TableHeader, TableBody, Th, Td } from "@/components/ui/table";
import { Badge, statusVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";
import { EmptyState } from "@/components/ui/empty-state";

export function ObservationTable({
  observations,
}: {
  observations: Record<string, ObservationSourceInfo> | undefined;
}) {
  const entries = observations ? Object.entries(observations) : [];

  return (
    <Card>
      <CardHeader>
        <span className="text-sm font-medium">Observation Health</span>
      </CardHeader>
      {entries.length === 0 ? (
        <EmptyState title="No observations" />
      ) : (
        <Table>
          <TableHeader>
            <Th>Source</Th>
            <Th>Status</Th>
            <Th>Last Seen</Th>
            <Th>Found</Th>
            <Th>Ingested</Th>
          </TableHeader>
          <TableBody>
            {entries.map(([source, info]) => (
              <tr key={source}>
                <Td className="font-medium text-white">{source}</Td>
                <Td>
                  <Badge variant={statusVariant(info.status)}>{info.status}</Badge>
                </Td>
                <Td>
                  <TimeAgo date={info.last_observed_at} className="text-xs" />
                </Td>
                <Td>{info.items_found}</Td>
                <Td>{info.items_ingested}</Td>
              </tr>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}
