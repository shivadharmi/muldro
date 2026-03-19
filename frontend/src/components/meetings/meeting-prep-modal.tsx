"use client";

import type { MeetingPrep } from "@/lib/types";
import { Badge } from "@/components/ui/badge";

export function MeetingPrepView({ prep }: { prep: MeetingPrep }) {
  return (
    <div className="space-y-4 max-h-[60vh] overflow-y-auto">
      <div>
        <h3 className="text-sm font-medium text-t-primary">{prep.title}</h3>
        {prep.starts_at && (
          <p className="text-xs text-t-tertiary mt-0.5">
            {new Date(prep.starts_at).toLocaleString()}
          </p>
        )}
      </div>

      {prep.attendees.length > 0 && (
        <Section title="Attendees">
          <div className="flex flex-wrap gap-1.5">
            {prep.attendees.map((a, i) => (
              <Badge key={i} variant="blue">
                {(a as Record<string, unknown>).name as string || `Attendee ${i + 1}`}
              </Badge>
            ))}
          </div>
        </Section>
      )}

      {prep.agenda.length > 0 && (
        <Section title="Agenda">
          <ol className="list-decimal list-inside space-y-1">
            {prep.agenda.map((item, i) => (
              <li key={i} className="text-xs text-t-primary">{item}</li>
            ))}
          </ol>
        </Section>
      )}

      {prep.related_threads.length > 0 && (
        <Section title="Related Threads">
          <div className="space-y-1.5">
            {prep.related_threads.map((thread, i) => (
              <div key={i} className="text-xs text-t-secondary">
                {(thread as Record<string, unknown>).subject as string ||
                  (thread as Record<string, unknown>).title as string ||
                  `Thread ${i + 1}`}
              </div>
            ))}
          </div>
        </Section>
      )}

      {prep.action_items.length > 0 && (
        <Section title="Action Items">
          <ul className="space-y-1">
            {prep.action_items.map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-t-primary">
                <span className="text-j-warning mt-0.5 shrink-0">&#x25CB;</span>
                <span>
                  {(item as Record<string, unknown>).description as string ||
                    (item as Record<string, unknown>).title as string ||
                    JSON.stringify(item)}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {prep.risks.length > 0 && (
        <Section title="Risks">
          <ul className="space-y-1">
            {prep.risks.map((risk, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-j-error">
                <span className="text-j-error mt-0.5 shrink-0">!</span>
                <span>{risk}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] uppercase text-t-muted mb-1.5 font-semibold tracking-wider">
        {title}
      </p>
      {children}
    </div>
  );
}
