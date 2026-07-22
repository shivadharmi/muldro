import type { A2UIComponent } from "@/lib/a2ui-types";
import { MarkdownRenderer } from "@/components/jarvis/markdown-renderer";

interface Props {
  component: A2UIComponent;
}

export function A2UIMarkdown({ component }: Props) {
  const content = (component.properties.content as string) || "";
  return <MarkdownRenderer content={content} />;
}
