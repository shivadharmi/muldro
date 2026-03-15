import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
}

const sizeClasses: Record<string, string> = {
  sm: "w-6 h-6 text-[10px]",
  md: "w-8 h-8 text-xs",
  lg: "w-10 h-10 text-sm",
};

export function A2UIAvatar({ component }: Props) {
  const name = (component.properties.name as string) || "";
  const url = component.properties.url as string | undefined;
  const size = (component.properties.size as string) || "md";
  const cls = sizeClasses[size] || sizeClasses.md;
  const initials = name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  if (url) {
    return (
      <img src={url} alt={name} className={`${cls} rounded-full object-cover`} />
    );
  }

  return (
    <div className={`${cls} rounded-full bg-blue-600 flex items-center justify-center font-medium text-white`}>
      {initials}
    </div>
  );
}
