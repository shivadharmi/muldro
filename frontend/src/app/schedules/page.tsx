"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchSchedules,
  createSchedule,
  pauseSchedule,
  resumeSchedule,
  deleteSchedule,
} from "@/lib/api";
import type { ScheduleCreateInput } from "@/lib/types";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { ScheduleList } from "@/components/schedules/schedule-list";
import { ScheduleForm } from "@/components/schedules/schedule-form";

export default function SchedulesPage() {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);

  const { data: schedules = [], isLoading } = useQuery({
    queryKey: ["schedules"],
    queryFn: fetchSchedules,
    refetchInterval: 60_000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["schedules"] });

  const createMut = useMutation({
    mutationFn: (input: ScheduleCreateInput) => createSchedule(input),
    onSuccess: () => {
      invalidate();
      setShowForm(false);
    },
  });

  const pauseMut = useMutation({
    mutationFn: pauseSchedule,
    onSuccess: invalidate,
  });

  const resumeMut = useMutation({
    mutationFn: resumeSchedule,
    onSuccess: invalidate,
  });

  const deleteMut = useMutation({
    mutationFn: deleteSchedule,
    onSuccess: invalidate,
  });

  return (
    <div className="p-6">
      <PageHeader
        title="Schedules"
        subtitle="Manage recurring and one-shot tasks"
        actions={
          <Button size="sm" onClick={() => setShowForm(true)}>
            Create Schedule
          </Button>
        }
      />

      {isLoading ? (
        <p className="text-neutral-500 text-sm">Loading...</p>
      ) : (
        <ScheduleList
          schedules={schedules}
          onPause={(id) => pauseMut.mutate(id)}
          onResume={(id) => resumeMut.mutate(id)}
          onDelete={(id) => deleteMut.mutate(id)}
        />
      )}

      <Modal open={showForm} onClose={() => setShowForm(false)} title="Create Schedule">
        <ScheduleForm
          onSubmit={(input) => createMut.mutate(input)}
          onCancel={() => setShowForm(false)}
        />
      </Modal>
    </div>
  );
}
