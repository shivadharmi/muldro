"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchMemories } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";

const INSTRUCTION_TYPES: Record<string, { label: string; variant: "blue" | "green" | "purple" }> = {
  trigger: { label: "Trigger", variant: "blue" },
  schedule: { label: "Schedule", variant: "green" },
  preference: { label: "Preference", variant: "purple" },
  goal: { label: "Goal", variant: "blue" },
};

export function PreferencesPanel() {
  const queryClient = useQueryClient();
  const { addToast } = useToast();

  const { data: prefData } = useQuery({
    queryKey: ["memories-preferences"],
    queryFn: () => fetchMemories("preference", 50),
  });

  const { data: goalData } = useQuery({
    queryKey: ["memories-goals"],
    queryFn: () => fetchMemories("goal", 50),
  });

  const preferences = prefData?.memories ?? [];
  const goals = goalData?.memories ?? [];

  const archiveMutation = useMutation({
    mutationFn: async (memoryId: string) => {
      const res = await fetch(`/api/memories/${memoryId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to archive");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memories-preferences"] });
      queryClient.invalidateQueries({ queryKey: ["memories-goals"] });
      addToast("Instruction removed", "success");
    },
    onError: () => addToast("Failed to remove instruction", "error"),
  });

  const allItems = [
    ...goals.map((g) => ({ ...g, _type: "goal" as const })),
    ...preferences.map((p) => ({ ...p, _type: "preference" as const })),
  ].filter((item) => item.status === "active");

  if (allItems.length === 0) {
    return (
      <div className="text-center py-8">
        <p className="text-sm text-t-secondary">No active instructions or goals</p>
        <p className="text-xs text-t-tertiary mt-1">
          Tell Jarvis what you care about in Chat — instructions will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-t-tertiary">
        These are things you&apos;ve told Jarvis to remember, watch for, or do regularly.
      </p>
      {allItems.map((item) => {
        const typeInfo = INSTRUCTION_TYPES[item._type] || INSTRUCTION_TYPES.preference;
        return (
          <Card key={item.memory_id}>
            <CardBody>
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant={typeInfo.variant}>{typeInfo.label}</Badge>
                    <span className="text-[10px] text-t-muted">
                      {item.created_at
                        ? new Date(item.created_at).toLocaleDateString()
                        : ""}
                    </span>
                  </div>
                  <p className="text-sm text-t-primary">{item.fact_text}</p>
                </div>
                <button
                  onClick={() => archiveMutation.mutate(item.memory_id)}
                  disabled={archiveMutation.isPending}
                  className="text-xs text-t-tertiary hover:text-j-error shrink-0 cursor-pointer"
                >
                  Remove
                </button>
              </div>
            </CardBody>
          </Card>
        );
      })}
    </div>
  );
}
