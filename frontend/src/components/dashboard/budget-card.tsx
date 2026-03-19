"use client";

import type { BudgetInfo } from "@/lib/types";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge, statusVariant } from "@/components/ui/badge";

export function BudgetCard({ budget }: { budget: BudgetInfo | undefined }) {
  if (!budget) return null;

  const pct = Math.min(budget.percent_used, 100);
  const barColor =
    pct >= 95 ? "bg-j-error" : pct >= 80 ? "bg-j-warning" : "bg-j-primary";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">Daily Budget</span>
          <Badge variant={statusVariant(budget.budget_mode)}>
            {budget.budget_mode}
          </Badge>
        </div>
      </CardHeader>
      <CardBody>
        <div className="flex items-center justify-between text-xs text-t-secondary mb-2">
          <span>${budget.daily_spend_usd.toFixed(2)} spent</span>
          <span>${budget.daily_limit_usd.toFixed(2)} limit</span>
        </div>
        <div className="h-2 bg-surface-2 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${barColor}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-xs text-t-tertiary mt-1">{budget.percent_used.toFixed(1)}% used</p>
      </CardBody>
    </Card>
  );
}
