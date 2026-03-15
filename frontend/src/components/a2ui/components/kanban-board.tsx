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
        <div key={i} className="flex-shrink-0 w-64 rounded-lg border border-neutral-800 bg-neutral-900/50">
          <div className="px-3 py-2 border-b border-neutral-800">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-medium text-neutral-400 uppercase">{col.title}</h4>
              <span className="text-[10px] text-neutral-600">{col.items.length}</span>
            </div>
          </div>
          <div className="p-2 space-y-2">
            {col.items.map((item) => (
              <div key={item.id} className="rounded border border-neutral-800 bg-neutral-900 p-2">
                <p className="text-sm text-white">{item.title}</p>
                {item.subtitle && (
                  <p className="text-xs text-neutral-500 mt-0.5">{item.subtitle}</p>
                )}
              </div>
            ))}
            {col.items.length === 0 && (
              <p className="text-xs text-neutral-600 text-center py-4">Empty</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
