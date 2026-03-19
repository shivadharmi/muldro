"use client";

import type { Schedule } from "@/lib/types";
import { Badge, priorityVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Td } from "@/components/ui/table";
import { TimeAgo } from "@/components/ui/time-ago";

export function ScheduleRow({
  schedule,
  onPause,
  onResume,
  onDelete,
}: {
  schedule: Schedule;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <tr>
      <Td className="font-medium text-white">{schedule.name}</Td>
      <Td>{schedule.action_type}</Td>
      <Td className="font-mono text-xs">{schedule.cron_expr || "--"}</Td>
      <Td>
        <Badge variant={schedule.enabled ? "green" : "red"}>
          {schedule.enabled ? "enabled" : "disabled"}
        </Badge>
      </Td>
      <Td><TimeAgo date={schedule.last_run_at} className="text-xs" /></Td>
      <Td><TimeAgo date={schedule.next_run_at} className="text-xs" /></Td>
      <Td>
        {schedule.consecutive_failures > 0 && (
          <Badge variant="red">{schedule.consecutive_failures}</Badge>
        )}
        {schedule.consecutive_failures === 0 && <span className="text-neutral-600">0</span>}
      </Td>
      <Td>
        <Badge variant={priorityVariant(schedule.priority)}>{schedule.priority}</Badge>
      </Td>
      <Td>
        <div className="flex items-center gap-1">
          {schedule.enabled ? (
            <Button size="sm" variant="ghost" onClick={() => onPause(schedule.schedule_id)}>
              Pause
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => onResume(schedule.schedule_id)}>
              Resume
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => onDelete(schedule.schedule_id)}>
            Delete
          </Button>
        </div>
      </Td>
    </tr>
  );
}
