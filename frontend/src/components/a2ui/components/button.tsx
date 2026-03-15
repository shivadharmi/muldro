import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
  onAction: (action: string, payload: Record<string, unknown>) => void;
}

const variantClasses: Record<string, string> = {
  primary: "bg-blue-600 hover:bg-blue-700 text-white",
  secondary: "bg-neutral-700 hover:bg-neutral-600 text-neutral-200",
  danger: "bg-red-600 hover:bg-red-700 text-white",
};

export function A2UIButton({ component, onAction }: Props) {
  const label = (component.properties.label as string) || "Button";
  const variant = (component.properties.variant as string) || "secondary";
  const disabled = component.properties.disabled as boolean;

  const handleClick = () => {
    if (component.actions.length > 0) {
      const action = component.actions[0];
      onAction(action.type, action.payload);
    }
  };

  return (
    <button
      onClick={handleClick}
      disabled={disabled}
      className={`px-3 py-1.5 rounded text-sm font-medium transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${variantClasses[variant] || variantClasses.secondary}`}
    >
      {label}
    </button>
  );
}
