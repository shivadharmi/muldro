import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UICodeBlock({ component }: Props) {
  const code = (component.properties.code as string) || "";
  const language = (component.properties.language as string) || "text";

  return (
    <div className="rounded-[var(--radius-lg)] bg-surface-1 border border-b-primary overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 bg-surface-2 border-b border-b-primary">
        <span className="text-[10px] text-t-tertiary uppercase">{language}</span>
      </div>
      <pre className="p-3 overflow-x-auto text-sm text-t-primary font-mono">
        <code>{code}</code>
      </pre>
    </div>
  );
}
