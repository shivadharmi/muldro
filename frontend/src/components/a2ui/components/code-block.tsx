import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

export function A2UICodeBlock({ component }: Props) {
  const code = (component.properties.code as string) || "";
  const language = (component.properties.language as string) || "text";

  return (
    <div className="rounded-lg bg-neutral-900 border border-neutral-800 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 bg-neutral-800/50 border-b border-neutral-800">
        <span className="text-[10px] text-neutral-500 uppercase">{language}</span>
      </div>
      <pre className="p-3 overflow-x-auto text-sm text-neutral-300 font-mono">
        <code>{code}</code>
      </pre>
    </div>
  );
}
