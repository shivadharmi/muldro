import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

interface KanbanColumn {
  title: string;
  items: Array<{ id: string; title: string; subtitle?: string }>;
}

export function A2UIKanbanBoard({ component }: Props) {
  const columns = (component.properties.columns as KanbanColumn[]) || [];

  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {columns.map((col, i) => (
        <div key={i} className="flex-shrink-0 w-64 rounded-lg border border-b-primary bg-surface-1/50">
          <div className="px-3 py-2 border-b border-b-primary">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-medium text-t-secondary uppercase">{col.title}</h4>
              <span className="text-[10px] text-t-muted">{col.items.length}</span>
            </div>
          </div>
          <div className="p-2 space-y-2">
            {col.items.map((item) => (
              <div key={item.id} className="rounded border border-b-primary bg-surface-1 p-2">
                <p className="text-sm text-t-primary">{item.title}</p>
                {item.subtitle && (
                  <p className="text-xs text-t-tertiary mt-0.5">{item.subtitle}</p>
                )}
              </div>
            ))}
            {col.items.length === 0 && (
              <p className="text-xs text-t-muted text-center py-4">Empty</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
