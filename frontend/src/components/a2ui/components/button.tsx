import type { A2UIComponent } from "@/lib/a2ui-types";

interface Props {
  component: A2UIComponent;
  onAction: (action: string, payload: Record<string, unknown>) => void;
}

const variantClasses: Record<string, string> = {
  primary: "bg-j-primary hover:bg-j-primary-hover text-j-primary-fg",
  secondary: "bg-surface-3 hover:bg-surface-2 text-t-primary",
  danger: "bg-j-error hover:opacity-90 text-j-primary-fg",
};

export function A2UIButton({ component, onAction }: Props) {
  const label = (component.properties.label as string) || "Button";
  const variant = (component.properties.variant as string) || "secondary";
  const disabled = component.properties.disabled as boolean;
  const loading = component.properties.loading as boolean;

  const handleClick = () => {
    if (loading || disabled) return;
    if (component.actions.length > 0) {
      const action = component.actions[0];
      onAction(action.type, action.payload);
    }
  };

  return (
    <button
      onClick={handleClick}
      disabled={disabled || loading}
      className={`px-3 py-1.5 rounded text-sm font-medium transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5 ${variantClasses[variant] || variantClasses.secondary}`}
    >
      {loading && (
        <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
      )}
      {label}
    </button>
  );
}
