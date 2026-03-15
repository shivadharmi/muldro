import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UITimeline({ component }: Props) {
  const events = (component.properties.events as Array<Record<string, string>>) || [];

  return (
    <div className="space-y-0">
      {events.map((evt, i) => (
        <div key={i} className="flex gap-3 relative">
          <div className="flex flex-col items-center">
            <div className="w-2 h-2 rounded-full bg-blue-500 mt-2 z-10" />
            {i < events.length - 1 && (
              <div className="w-px flex-1 bg-neutral-700" />
            )}
          </div>
          <div className="pb-4 min-w-0">
            <p className="text-xs text-neutral-500">{evt.time || ""}</p>
            <p className="text-sm text-white">{evt.title || ""}</p>
            {evt.source && (
              <p className="text-xs text-neutral-500">{evt.source}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
