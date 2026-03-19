"use client";

import type { BudgetInfo, QueueInfo } from "@/lib/types";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, statusVariant } from "@/components/ui/badge";

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: React.ReactNode;
}) {
  return (
    <Card>
      <CardBody>
        <p className="text-xs text-t-tertiary mb-1">{label}</p>
        <p className="text-2xl font-semibold">{value}</p>
        {sub && <div className="mt-1">{sub}</div>}
      </CardBody>
    </Card>
  );
}

export function OverviewStats({
  budget,
  queues,
}: {
  budget: BudgetInfo | undefined;
  queues: QueueInfo | undefined;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard
        label="Budget Used"
        value={`${budget?.percent_used?.toFixed(1) ?? 0}%`}
        sub={
          budget && (
            <Badge variant={statusVariant(budget.budget_mode)}>
              {budget.budget_mode}
            </Badge>
          )
        }
      />
      <StatCard
        label="Pending Approvals"
        value={queues?.approvals_pending ?? 0}
      />
      <StatCard
        label="Active Tasks"
        value={queues?.plans_in_flight ?? 0}
      />
      <StatCard
        label="DLQ Pending"
        value={queues?.dlq_pending ?? 0}
      />
    </div>
  );
}
