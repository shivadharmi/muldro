import type { A2UIComponent } from "@/lib/a2ui-types";
import { InlineMarkdown, MarkdownRenderer } from "@/components/muldro/markdown-renderer";

interface Props {
  component: A2UIComponent;
}

const variantClasses: Record<string, string> = {
  heading: "text-lg font-semibold text-t-primary",
  body: "text-sm text-t-primary",
  caption: "text-xs text-t-tertiary",
};

export function A2UIText({ component }: Props) {
  const variant = (component.properties.variant as string) || "body";
  const text = (component.properties.text as string) || "";
  const className = variantClasses[variant] || variantClasses.body;

  if (variant === "caption") {
    return (
      <span className={className}>
        <InlineMarkdown content={text} />
      </span>
    );
  }

  return (
    <div className={className}>
      <MarkdownRenderer content={text} />
    </div>
  );
}
