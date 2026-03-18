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
import { useToast } from "@/components/ui/toast";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { ScheduleList } from "@/components/schedules/schedule-list";
import { ScheduleForm } from "@/components/schedules/schedule-form";

export default function SchedulesPage() {
  const queryClient = useQueryClient();
  const { addToast } = useToast();
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
      addToast("Schedule created", "success");
    },
    onError: (err) => addToast(`Failed to create schedule: ${err.message}`, "error"),
  });

  const pauseMut = useMutation({
    mutationFn: pauseSchedule,
    onSuccess: () => {
      invalidate();
      addToast("Schedule paused", "success");
    },
    onError: (err) => addToast(`Failed to pause schedule: ${err.message}`, "error"),
  });

  const resumeMut = useMutation({
    mutationFn: resumeSchedule,
    onSuccess: () => {
      invalidate();
      addToast("Schedule resumed", "success");
    },
    onError: (err) => addToast(`Failed to resume schedule: ${err.message}`, "error"),
  });

  const deleteMut = useMutation({
    mutationFn: deleteSchedule,
    onSuccess: () => {
      invalidate();
      addToast("Schedule deleted", "success");
    },
    onError: (err) => addToast(`Failed to delete schedule: ${err.message}`, "error"),
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
