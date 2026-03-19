"use client";

import type { TraceDetail } from "@/lib/types";
import { Badge } from "@/components/ui/badge";

export function TracePanel({ trace }: { trace: TraceDetail | null }) {
  if (!trace) {
    return <p className="text-xs text-t-muted">No trace data</p>;
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-[10px] uppercase text-t-muted mb-1">Cost</p>
        <p className="text-lg font-semibold text-t-primary">
          ${trace.total_cost_usd.toFixed(4)}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div>
          <p className="text-t-muted">Input tokens</p>
          <p className="text-t-primary">{trace.total_input_tokens.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-t-muted">Output tokens</p>
          <p className="text-t-primary">{trace.total_output_tokens.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-t-muted">Cache creation</p>
          <p className="text-t-primary">{trace.total_cache_creation_tokens.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-t-muted">Cache read</p>
          <p className="text-t-primary">{trace.total_cache_read_tokens.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-t-muted">Thinking</p>
          <p className="text-t-primary">{trace.total_thinking_tokens.toLocaleString()}</p>
        </div>
        <div>
          <p className="text-t-muted">Duration</p>
          <p className="text-t-primary">{(trace.duration_ms / 1000).toFixed(1)}s</p>
        </div>
      </div>

      {trace.agents_invoked.length > 0 && (
        <div>
          <p className="text-[10px] uppercase text-t-muted mb-1">Agents</p>
          <div className="flex flex-wrap gap-1">
            {trace.agents_invoked.map((a) => (
              <Badge key={a} variant="blue">{a}</Badge>
            ))}
          </div>
        </div>
      )}

      {trace.tools_called.length > 0 && (
        <div>
          <p className="text-[10px] uppercase text-t-muted mb-1">Tools</p>
          <div className="flex flex-wrap gap-1">
            {trace.tools_called.map((t) => (
              <Badge key={t} variant="purple">{t}</Badge>
            ))}
          </div>
        </div>
      )}

      {trace.spans && trace.spans.length > 0 && (
        <div>
          <p className="text-[10px] uppercase text-t-muted mb-1">Spans ({trace.spans.length})</p>
          <div className="space-y-2">
            {trace.spans.map((span) => (
              <div key={span.span_id} className="rounded bg-surface-2 p-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-t-primary font-medium">{span.agent_name}</span>
                  <span className="text-[10px] text-t-tertiary">{(span.duration_ms / 1000).toFixed(1)}s</span>
                </div>
                <div className="flex items-center gap-2 mt-0.5 text-[10px] text-t-tertiary">
                  <span>${span.cost_usd.toFixed(4)}</span>
                  {span.error && <span className="text-j-error">error</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
