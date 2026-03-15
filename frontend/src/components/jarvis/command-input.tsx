"use client";

import { useState } from "react";

interface Props {
  onSubmit: (message: string) => void;
  disabled?: boolean;
}

export function CommandInput({ onSubmit, disabled }: Props) {
  const [value, setValue] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim() && !disabled) {
      onSubmit(value.trim());
      setValue("");
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex gap-2">
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Ask Jarvis anything..."
        disabled={disabled}
        className="flex-1 rounded-lg bg-neutral-800 border border-neutral-700 px-4 py-3 text-sm text-white placeholder-neutral-500 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        className="rounded-lg bg-blue-600 px-4 py-3 text-sm font-medium text-white hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
      >
        Send
      </button>
    </form>
  );
}
