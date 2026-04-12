"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useCommandStore } from "@/stores/command-store";

interface Props {
  onSubmit: (message: string) => void;
  disabled?: boolean;
}

const COMMANDS = [
  { name: "/brief", description: "Today's briefing" },
  { name: "/status", description: "System health", endpoint: "/v1/health" },
  { name: "/search", description: "Search knowledge", params: ["query"] },
  { name: "/help", description: "Available commands" },
  { name: "/goals", description: "Active goals" },
];

export function CommandInput({ onSubmit, disabled }: Props) {
  const [value, setValue] = useState("");
  const [paletteHidden, setPaletteHidden] = useState(false);
  const [cmdKOpen, setCmdKOpen] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

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

  // Pending command from command launcher:
  // Set the input value, then programmatically submit the form.
  // This triggers React's synthetic event pipeline — same path as manual Enter.
  useEffect(() => {
    const { pendingCommand, setPendingCommand } = useCommandStore.getState();
    if (!pendingCommand || disabled) return;

    // Consume immediately
    const cmd = pendingCommand;
    setPendingCommand(null);

    // Set value and submit on next frame (after React renders the value)
    requestAnimationFrame(() => {
      setValue(cmd);
      requestAnimationFrame(() => {
        formRef.current?.requestSubmit();
      });
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Also subscribe for when user is already on /chat and uses launcher
  useEffect(() => {
    const unsub = useCommandStore.subscribe((state, prev) => {
      if (state.pendingCommand && state.pendingCommand !== prev.pendingCommand && !disabled) {
        const cmd = state.pendingCommand;
        useCommandStore.getState().setPendingCommand(null);

        setValue(cmd);
        requestAnimationFrame(() => {
          formRef.current?.requestSubmit();
        });
      }
    });
    return unsub;
  }, [disabled]);

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
        } rounded-[var(--radius-lg)] bg-surface-2 border border-b-primary shadow-lg overflow-hidden`}>
          {cmdKOpen && (
            <div className="px-4 py-2 border-b border-b-primary flex items-center gap-2">
              <span className="text-t-tertiary text-xs">⌘K</span>
              <input
                ref={cmdKOpen ? inputRef : undefined}
                type="text"
                value={value}
                onChange={(e) => { setValue(e.target.value); setPaletteHidden(false); }}
                onKeyDown={handleKeyDown}
                placeholder="Type a command..."
                autoFocus
                className="flex-1 bg-transparent text-sm text-t-primary placeholder-t-tertiary focus:outline-none"
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
                    ? "bg-j-primary-soft text-t-primary"
                    : "text-t-primary hover:bg-surface-3"
                }`}
              >
                <span className="font-mono">{cmd.name}</span>
                <span className="text-t-tertiary text-xs">{cmd.description}</span>
              </button>
            ))}
            {filtered.length === 0 && cmdKOpen && (
              <div className="px-4 py-3 text-sm text-t-tertiary">No matching commands</div>
            )}
          </div>
        </div>
      )}
      <form ref={formRef} onSubmit={handleSubmit} className="flex gap-2">
        <input
          ref={cmdKOpen ? undefined : inputRef}
          type="text"
          value={cmdKOpen ? "" : value}
          onChange={(e) => { if (!cmdKOpen) { setValue(e.target.value); setPaletteHidden(false); } }}
          onKeyDown={cmdKOpen ? undefined : handleKeyDown}
          placeholder="Ask Jarvis anything... (⌘K for commands, / for slash)"
          disabled={disabled}
          className="flex-1 rounded-[var(--radius-lg)] bg-surface-2 border border-b-primary px-4 py-3 text-sm text-t-primary placeholder-t-tertiary focus:outline-none focus:ring-2 focus:ring-j-ring disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={disabled || !value.trim() || cmdKOpen}
          className="rounded-[var(--radius-lg)] bg-j-primary px-4 py-3 text-sm font-medium text-j-primary-fg hover:bg-j-primary-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
        >
          Send
        </button>
      </form>
    </div>
  );
}
