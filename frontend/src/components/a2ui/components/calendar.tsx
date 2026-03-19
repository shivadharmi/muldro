import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

interface CalendarEvent {
  title: string;
  time?: string;
  date?: string;
}

export function A2UICalendar({ component }: Props) {
  const events = (component.properties.events as CalendarEvent[]) || [];
  const view = (component.properties.view as string) || "week";

  return (
    <div className="rounded-lg border border-b-primary bg-surface-1 p-4">
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-medium text-t-primary">Calendar</p>
        <span className="text-[10px] text-t-tertiary uppercase">{view} view</span>
      </div>
      <div className="space-y-2">
        {events.map((evt, i) => (
          <div key={i} className="flex items-center gap-3 rounded border border-b-primary p-2">
            <div className="w-1 h-8 rounded bg-j-primary" />
            <div>
              <p className="text-sm text-t-primary">{evt.title}</p>
              <p className="text-xs text-t-tertiary">{evt.time || evt.date || ""}</p>
            </div>
          </div>
        ))}
        {events.length === 0 && (
          <p className="text-xs text-t-muted text-center py-4">No events</p>
        )}
      </div>
    </div>
  );
}
