"use client";

import { Card, CardBody } from "@/components/ui/card";

/**
 * Placeholder. The Providers tab exists in the rail from this change onward so
 * the seventh tab, its icon and its connected/total suffix are wired end to
 * end; the schema-driven credential rows (§4.5 / §9.8) land in a later task.
 * Provider credentials remain editable in the Model tab until then.
 */
export function ProvidersTab() {
  return (
    <Card>
      <CardBody>
        <div className="py-2">
          <p className="text-sm text-t-secondary font-medium mb-1">
            Providers move here next
          </p>
          <p className="text-xs text-t-muted leading-relaxed">
            Connecting, testing and revoking provider credentials still lives in
            the Model tab. This tab takes it over in the next change.
          </p>
        </div>
      </CardBody>
    </Card>
  );
}
