"use client";

import type { Schedule } from "@/lib/types";
import { Table, TableHeader, TableBody, Th } from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";
import { ScheduleRow } from "./schedule-row";

export function ScheduleList({
  schedules,
  onPause,
  onResume,
  onDelete,
}: {
  schedules: Schedule[];
  onPause: (id: string) => void;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  if (schedules.length === 0) {
    return <EmptyState title="No schedules" description="Create a schedule to automate tasks" />;
  }

  return (
    <Table>
      <TableHeader>
        <Th>Name</Th>
        <Th>Action</Th>
        <Th>Cron</Th>
        <Th>Status</Th>
        <Th>Last Run</Th>
        <Th>Next Run</Th>
        <Th>Failures</Th>
        <Th>Priority</Th>
        <Th>Actions</Th>
      </TableHeader>
      <TableBody>
        {schedules.map((s) => (
          <ScheduleRow
            key={s.schedule_id}
            schedule={s}
            onPause={onPause}
            onResume={onResume}
            onDelete={onDelete}
          />
        ))}
      </TableBody>
    </Table>
  );
}
