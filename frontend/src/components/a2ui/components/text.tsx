import type { A2UIComponent } from "@/lib/a2ui-types";

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

  return <p className={className}>{text}</p>;
}
