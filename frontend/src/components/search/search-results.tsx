import type { SearchResult } from "@/lib/types";
import { Card, CardBody } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { InlineMarkdown } from "@/components/jarvis/markdown-renderer";
import Link from "next/link";

const TYPE_VARIANT: Record<string, "blue" | "green" | "purple"> = {
  memory: "blue",
  entity: "green",
  event: "purple",
};

function resultHref(r: SearchResult): string | null {
  switch (r.type) {
    case "memory":
      return "/memories";
    case "entity":
      return `/entities`;
    case "event":
      return null;
    default:
      return null;
  }
}

export function SearchResults({ results }: { results: SearchResult[] }) {
  if (results.length === 0) {
    return <EmptyState title="No results" description="Try a different query or scope" />;
  }

  return (
    <div className="space-y-3">
      {results.map((r) => {
        const href = resultHref(r);
        const content = (
          <Card className={href ? "hover:border-b-primary transition-colors" : ""}>
            <CardBody>
              <div className="flex items-start justify-between">
                <div className="flex items-start gap-2 flex-1">
                  <ResultIcon type={r.type} />
                  <div>
                    <p className="text-sm font-medium">{r.title}</p>
                    {r.summary && (
                      <div className="text-xs text-t-secondary mt-1">
                        <InlineMarkdown content={r.summary} />
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-3">
                  <Badge variant={TYPE_VARIANT[r.type] || "default"}>{r.type}</Badge>
                  {r.score !== null && (
                    <span className="text-xs text-t-muted">
                      {(r.score * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
              </div>
            </CardBody>
          </Card>
        );

        return href ? (
          <Link key={r.id} href={href} className="block">
            {content}
          </Link>
        ) : (
          <div key={r.id}>{content}</div>
        );
      })}
    </div>
  );
}

function ResultIcon({ type }: { type: string }) {
  const className = "w-4 h-4 mt-0.5 shrink-0";
  switch (type) {
    case "memory":
      return (
        <svg className={className} viewBox="0 0 16 16" fill="none">
          <path d="M4 3h8a1 1 0 011 1v8a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1z" stroke="currentColor" strokeWidth="1.5" className="text-j-primary" />
        </svg>
      );
    case "entity":
      return (
        <svg className={className} viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="4" r="2" stroke="currentColor" strokeWidth="1.5" className="text-j-success" />
          <circle cx="4" cy="12" r="2" stroke="currentColor" strokeWidth="1.5" className="text-j-success" />
          <circle cx="12" cy="12" r="2" stroke="currentColor" strokeWidth="1.5" className="text-j-success" />
        </svg>
      );
    case "event":
      return (
        <svg className={className} viewBox="0 0 16 16" fill="none">
          <path d="M9 2L5 9h3l-1 5 5-7H9l1-5z" stroke="currentColor" strokeWidth="1.5" className="text-j-secondary" />
        </svg>
      );
    default:
      return null;
  }
}
