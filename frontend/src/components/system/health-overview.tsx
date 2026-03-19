"use client";

import type { SystemDashboard } from "@/lib/types";
import { BudgetCard } from "@/components/dashboard/budget-card";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, statusVariant } from "@/components/ui/badge";

export function HealthOverview({ data }: { data: SystemDashboard }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <BudgetCard budget={data.budget} />

        <Card>
          <CardHeader>
            <span className="text-sm font-medium">Queue Depths</span>
          </CardHeader>
          <CardBody className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-t-secondary">DLQ Pending</span>
              <span>{data.queues.dlq_pending}</span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-t-secondary">Approvals Pending</span>
              <span>{data.queues.approvals_pending}</span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-t-secondary">Plans In Flight</span>
              <span>{data.queues.plans_in_flight}</span>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <span className="text-sm font-medium">Overall Status</span>
          </CardHeader>
          <CardBody className="flex items-center justify-center py-6">
            <div className="text-center">
              <Badge variant={statusVariant(data.status)} className="text-sm px-3 py-1">
                {data.status.toUpperCase()}
              </Badge>
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
