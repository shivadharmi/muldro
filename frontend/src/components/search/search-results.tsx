import type { SearchResult } from "@/lib/types";
import { Card, CardBody } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";

const TYPE_VARIANT: Record<string, "blue" | "green" | "purple"> = {
  memory: "blue",
  entity: "green",
  event: "purple",
};

export function SearchResults({ results }: { results: SearchResult[] }) {
  if (results.length === 0) {
    return <EmptyState title="No results" description="Try a different query or scope" />;
  }

  return (
    <div className="space-y-3">
      {results.map((r) => (
        <Card key={r.id}>
          <CardBody>
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <p className="text-sm font-medium">{r.title}</p>
                {r.summary && (
                  <p className="text-xs text-neutral-400 mt-1">{r.summary}</p>
                )}
              </div>
              <div className="flex items-center gap-2 ml-3">
                <Badge variant={TYPE_VARIANT[r.type] || "default"}>{r.type}</Badge>
                {r.score !== null && (
                  <span className="text-xs text-neutral-600">
                    {(r.score * 100).toFixed(0)}%
                  </span>
                )}
              </div>
            </div>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}
