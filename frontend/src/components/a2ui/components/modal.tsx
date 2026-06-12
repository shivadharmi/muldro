import type { A2UIComponent } from "@/lib/a2ui-types";
import type { ReactNode } from "react";
import { InlineMarkdown } from "@/components/jarvis/markdown-renderer";

interface Props {
  component: A2UIComponent;
  children: ReactNode;
}

export function A2UIModal({ component, children }: Props) {
  const title = (component.properties.title as string) || "";
  const open = component.properties.open !== false;

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="rounded-[var(--radius-lg)] border border-b-primary bg-surface-1 p-5 max-w-lg w-full mx-4 shadow-xl">
        {title && (
          <h3 className="text-lg font-semibold text-t-primary mb-3">
            <InlineMarkdown content={title} />
          </h3>
        )}
        <div className="space-y-2">{children}</div>
      </div>
    </div>
  );
}
