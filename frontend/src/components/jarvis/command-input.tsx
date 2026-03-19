"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface Props {
  onSubmit: (message: string) => void;
  disabled?: boolean;
}

const COMMANDS = [
  { name: "/brief", description: "Morning briefing", endpoint: "/v1/briefings/latest" },
  { name: "/status", description: "System health", endpoint: "/v1/health" },
  { name: "/search", description: "Search memories", params: ["query"] },
  { name: "/help", description: "Available commands" },
  { name: "/agents", description: "List agents", endpoint: "/v1/agents" },
  { name: "/runs", description: "Recent runs" },
  { name: "/goals", description: "Active goals" },
  { name: "/triggers", description: "Active triggers" },
];

export function CommandInput({ onSubmit, disabled }: Props) {
  const [value, setValue] = useState("");
  const [paletteHidden, setPaletteHidden] = useState(false);
  const [cmdKOpen, setCmdKOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = (cmdKOpen || value.startsWith("/"))
    ? COMMANDS.filter((c) => {
        const search = cmdKOpen ? value.toLowerCase() : value.split(" ")[0].toLowerCase();
        return c.name.includes(search) || c.description.toLowerCase().includes(search);
      })
    : [];

  // Derive showPalette from state — no effect needed
  const showPalette = !paletteHidden && (cmdKOpen || (value.startsWith("/") && filtered.length > 0));

  // Global Cmd+K / Ctrl+K listener
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setCmdKOpen((prev) => !prev);
        if (!cmdKOpen) {
          setValue("");
          setTimeout(() => inputRef.current?.focus(), 50);
        }
      }
      if (e.key === "Escape" && cmdKOpen) {
        setCmdKOpen(false);
        setPaletteHidden(true);
      }
    };
    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [cmdKOpen]);

  const executeCommand = useCallback(
    (cmd: (typeof COMMANDS)[number]) => {
      const parts = value.split(" ");
      const args = cmdKOpen ? "" : parts.slice(1).join(" ");

      if (cmd.params && cmd.params.length > 0 && !args) {
        setValue(cmd.name + " ");
        setPaletteHidden(true);
        setCmdKOpen(false);
        inputRef.current?.focus();
        return;
      }

      onSubmit(args ? `${cmd.name} ${args}` : cmd.name);
      setValue("");
      setCmdKOpen(false);
    },
    [value, onSubmit, cmdKOpen],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!showPalette) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => Math.min(prev + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === "Enter" && filtered[selectedIndex]) {
      e.preventDefault();
      executeCommand(filtered[selectedIndex]);
    } else if (e.key === "Escape") {
      setPaletteHidden(true);
      setCmdKOpen(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim() && !disabled) {
      onSubmit(value.trim());
      setValue("");
      setCmdKOpen(false);
    }
  };

  return (
    <div className="relative">
      {/* Cmd+K overlay backdrop */}
      {cmdKOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40"
          onClick={() => { setCmdKOpen(false); setPaletteHidden(true); }}
        />
      )}
      {showPalette && (
        <div className={`${
          cmdKOpen
            ? "fixed top-1/4 left-1/2 -translate-x-1/2 w-[480px] z-50"
            : "absolute bottom-full left-0 right-0 mb-1 z-10"
        } rounded-lg bg-neutral-800 border border-neutral-700 shadow-lg overflow-hidden`}>
          {cmdKOpen && (
            <div className="px-4 py-2 border-b border-neutral-700 flex items-center gap-2">
              <span className="text-neutral-500 text-xs">⌘K</span>
              <input
                ref={cmdKOpen ? inputRef : undefined}
                type="text"
                value={value}
                onChange={(e) => { setValue(e.target.value); setPaletteHidden(false); }}
                onKeyDown={handleKeyDown}
                placeholder="Type a command..."
                autoFocus
                className="flex-1 bg-transparent text-sm text-white placeholder-neutral-500 focus:outline-none"
              />
            </div>
          )}
          <div className="max-h-64 overflow-y-auto">
            {filtered.map((cmd, i) => (
              <button
                key={cmd.name}
                type="button"
                onClick={() => executeCommand(cmd)}
                className={`w-full text-left px-4 py-2 text-sm flex justify-between items-center cursor-pointer ${
                  i === selectedIndex
                    ? "bg-blue-600/30 text-white"
                    : "text-neutral-300 hover:bg-neutral-700"
                }`}
              >
                <span className="font-mono">{cmd.name}</span>
                <span className="text-neutral-500 text-xs">{cmd.description}</span>
              </button>
            ))}
            {filtered.length === 0 && cmdKOpen && (
              <div className="px-4 py-3 text-sm text-neutral-500">No matching commands</div>
            )}
          </div>
        </div>
      )}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          ref={cmdKOpen ? undefined : inputRef}
          type="text"
          value={cmdKOpen ? "" : value}
          onChange={(e) => { if (!cmdKOpen) { setValue(e.target.value); setPaletteHidden(false); } }}
          onKeyDown={cmdKOpen ? undefined : handleKeyDown}
          placeholder="Ask Jarvis anything... (⌘K for commands, / for slash)"
          disabled={disabled}
          className="flex-1 rounded-lg bg-neutral-800 border border-neutral-700 px-4 py-3 text-sm text-white placeholder-neutral-500 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={disabled || !value.trim() || cmdKOpen}
          className="rounded-lg bg-blue-600 px-4 py-3 text-sm font-medium text-white hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
        >
          Send
        </button>
      </form>
    </div>
  );
}
