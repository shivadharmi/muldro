"use client";

import type { AgentUsageInfo } from "@/lib/types";
import { Card, CardHeader } from "@/components/ui/card";
import { Table, TableHeader, TableBody, Th, Td } from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";

export function AgentUsageTable({
  agents,
}: {
  agents: Record<string, AgentUsageInfo> | undefined;
}) {
  const entries = agents ? Object.entries(agents) : [];

  return (
    <Card>
      <CardHeader>
        <span className="text-sm font-medium">Agent Usage (Today)</span>
      </CardHeader>
      {entries.length === 0 ? (
        <EmptyState title="No agent activity today" />
      ) : (
        <Table>
          <TableHeader>
            <Th>Agent</Th>
            <Th>Calls</Th>
            <Th>Input Tokens</Th>
            <Th>Output Tokens</Th>
            <Th>Cost</Th>
          </TableHeader>
          <TableBody>
            {entries.map(([name, info]) => (
              <tr key={name}>
                <Td className="font-medium text-white capitalize">{name}</Td>
                <Td>{info.calls_today}</Td>
                <Td>{info.total_input_tokens.toLocaleString()}</Td>
                <Td>{info.total_output_tokens.toLocaleString()}</Td>
                <Td>${info.total_cost_usd.toFixed(4)}</Td>
              </tr>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}
