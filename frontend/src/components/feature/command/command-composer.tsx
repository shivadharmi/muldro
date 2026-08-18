"use client";

import { useState, useCallback } from "react";
import { useCommandStore } from "@/stores/command-store";

interface Props {
  onSend: (message: string) => void;
  disabled?: boolean;
}

const modes = [
  { value: "auto", label: "Auto" },
  { value: "ask", label: "Ask" },
  { value: "bypass", label: "Bypass" },
] as const;

export function CommandComposer({ onSend, disabled }: Props) {
  const { permissionMode, setPermissionMode } = useCommandStore();
  const [input, setInput] = useState("");

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = input.trim();
      if (!trimmed || disabled) return;
      onSend(trimmed);
      setInput("");
    },
    [input, disabled, onSend]
  );

  return (
    <form onSubmit={handleSubmit} className="border-t border-b-primary p-3">
      {/* Mode Selector */}
      <div className="flex items-center gap-1 mb-2">
        {modes.map((m) => (
          <button
            key={m.value}
            type="button"
            onClick={() => setPermissionMode(m.value)}
            className={`px-2.5 py-1 text-xs rounded-[var(--radius-sm)] transition-colors cursor-pointer ${
              permissionMode === m.value
                ? "bg-accent-primary text-white"
                : "text-t-tertiary hover:text-t-secondary hover:bg-surface-1"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Input */}
      <div className="flex items-end gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            permissionMode === "bypass" ? "What should Muldro do?" : "Ask Muldro anything..."
          }
          rows={1}
          className="flex-1 bg-surface-1 border border-b-primary rounded-[var(--radius-md)] px-3 py-2 text-sm text-t-primary placeholder:text-t-tertiary resize-none focus:outline-none focus:ring-1 focus:ring-accent-primary"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSubmit(e);
            }
          }}
        />
        <button
          type="submit"
          disabled={disabled || !input.trim()}
          className="px-4 py-2 bg-accent-primary text-white text-sm rounded-[var(--radius-md)] hover:opacity-90 transition-opacity disabled:opacity-40 cursor-pointer"
        >
          Send
        </button>
      </div>
    </form>
  );
}
