"use client";

import type { ObservationStatus } from "@/lib/types";
import { Table, TableHeader, TableBody, Th, Td } from "@/components/ui/table";
import { Badge, statusVariant } from "@/components/ui/badge";
import { TimeAgo } from "@/components/ui/time-ago";
import { EmptyState } from "@/components/ui/empty-state";

export function ObservationHealth({
  observations,
}: {
  observations: ObservationStatus[];
}) {
  if (observations.length === 0) {
    return <EmptyState title="No observations reported" />;
  }

  return (
    <Table>
      <TableHeader>
        <Th>Source</Th>
        <Th>Status</Th>
        <Th>Stale</Th>
        <Th>Last Observed</Th>
        <Th>Items Found</Th>
        <Th>Items Ingested</Th>
        <Th>Error</Th>
      </TableHeader>
      <TableBody>
        {observations.map((obs) => (
          <tr key={obs.source}>
            <Td className="font-medium text-t-primary">{obs.source}</Td>
            <Td>
              <Badge variant={statusVariant(obs.status)}>{obs.status}</Badge>
            </Td>
            <Td>
              {obs.is_stale ? (
                <Badge variant="red">stale</Badge>
              ) : (
                <Badge variant="green">fresh</Badge>
              )}
            </Td>
            <Td>
              <TimeAgo date={obs.last_observed_at} className="text-xs" />
            </Td>
            <Td>{obs.items_found}</Td>
            <Td>{obs.items_ingested}</Td>
            <Td>
              {obs.error_message ? (
                <span className="text-xs text-j-error">{obs.error_message}</span>
              ) : (
                <span className="text-t-muted">--</span>
              )}
            </Td>
          </tr>
        ))}
      </TableBody>
    </Table>
  );
}
