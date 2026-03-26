"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useShellStore } from "@/stores/shell-store";
import { useCommandStore, type CommandMode } from "@/stores/command-store";

const COMMANDS = [
  { name: "/brief", description: "Morning briefing", route: "/briefings" },
  { name: "/status", description: "System health", route: "/system" },
  { name: "/search", description: "Search memories", route: "/search" },
  { name: "/agents", description: "List agents", route: "/agents" },
  { name: "/runs", description: "Recent runs", route: "/runs" },
  { name: "/goals", description: "Active goals", route: "/goals" },
  { name: "/triggers", description: "Active triggers", route: "/triggers" },
  { name: "/approvals", description: "Pending approvals", route: "/approvals" },
] as const;

const MODES: { value: CommandMode; label: string; icon: string }[] = [
  { value: "ask", label: "Ask", icon: "?" },
  { value: "plan", label: "Plan", icon: "◈" },
  { value: "execute", label: "Execute", icon: "▶" },
];

export function CommandLauncher() {
  const { commandLauncherOpen, closeCommandLauncher } = useShellStore();
  const { mode, setMode } = useCommandStore();
  const [value, setValue] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const handleClose = useCallback(() => {
    closeCommandLauncher();
    setValue("");
    setSelectedIndex(0);
  }, [closeCommandLauncher]);

  const isSlash = value.startsWith("/");
  const filtered = isSlash
    ? COMMANDS.filter(
        (c) =>
          c.name.includes(value.toLowerCase()) ||
          c.description.toLowerCase().includes(value.toLowerCase())
      )
    : [];

  // Focus input when launcher opens
  useEffect(() => {
    if (commandLauncherOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [commandLauncherOpen]);

  const executeSlashCommand = useCallback(
    (cmd: (typeof COMMANDS)[number]) => {
      if (cmd.route) {
        router.push(cmd.route);
      }
      handleClose();
    },
    [router, handleClose]
  );

  const { setPendingCommand } = useCommandStore();

  const submitMessage = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed) return;

    // Set pending command in store, then navigate to chat
    setPendingCommand(trimmed);
    router.push("/chat");
    handleClose();
  }, [value, router, handleClose, setPendingCommand]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      handleClose();
      return;
    }

    if (isSlash && filtered.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => Math.min(prev + 1, filtered.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => Math.max(prev - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        executeSlashCommand(filtered[selectedIndex]);
      }
    } else if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitMessage();
    }
  };

  if (!commandLauncherOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-50 backdrop-blur-sm"
        onClick={handleClose}
      />

      {/* Launcher — centered on desktop, bottom sheet on mobile */}
      <div className="fixed z-50 inset-x-0 bottom-0 sm:bottom-auto sm:top-[20%] sm:left-1/2 sm:-translate-x-1/2 sm:w-full sm:max-w-[560px]">
        <div className="sm:mx-4 rounded-t-xl sm:rounded-xl bg-surface-1 border border-b-primary shadow-lg overflow-hidden">
          {/* Mode bar */}
          <div className="flex items-center gap-1 px-4 pt-3 pb-1">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => setMode(m.value)}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-full transition-colors cursor-pointer ${
                  mode === m.value
                    ? "bg-accent-primary text-white"
                    : "text-t-tertiary hover:text-t-secondary hover:bg-surface-2"
                }`}
              >
                <span>{m.icon}</span>
                <span>{m.label}</span>
              </button>
            ))}
            <div className="flex-1" />
            <span className="text-[10px] text-t-tertiary px-2 py-1 rounded bg-surface-2">⌘K</span>
          </div>

          {/* Input — taller on mobile for touch targets */}
          <div className="px-4 py-3 sm:py-3">
            <input
              ref={inputRef}
              type="text"
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                setSelectedIndex(0);
              }}
              onKeyDown={handleKeyDown}
              placeholder={
                mode === "ask"
                  ? "Ask Jarvis anything..."
                  : mode === "plan"
                    ? "Describe what you want to plan..."
                    : "What should Jarvis execute?"
              }
              className="w-full bg-transparent text-base sm:text-sm text-t-primary placeholder-t-tertiary focus:outline-none"
              autoFocus
            />
          </div>

          {/* Slash command palette */}
          {isSlash && filtered.length > 0 && (
            <div className="border-t border-b-primary max-h-64 overflow-y-auto">
              {filtered.map((cmd, i) => (
                <button
                  key={cmd.name}
                  type="button"
                  onClick={() => executeSlashCommand(cmd)}
                  className={`w-full text-left px-4 py-2.5 text-sm flex justify-between items-center cursor-pointer ${
                    i === selectedIndex
                      ? "bg-accent-primary/10 text-t-primary"
                      : "text-t-primary hover:bg-surface-2"
                  }`}
                >
                  <span className="font-mono text-accent-primary">{cmd.name}</span>
                  <span className="text-t-tertiary text-xs">{cmd.description}</span>
                </button>
              ))}
            </div>
          )}

          {/* Footer — hints on desktop, send button on mobile */}
          <div className="px-4 py-2 border-t border-b-primary flex items-center justify-between">
            <div className="hidden sm:flex items-center gap-3 text-[10px] text-t-tertiary">
              <span>↵ Send</span>
              <span>/ Commands</span>
              <span>Esc Close</span>
            </div>
            <button
              type="button"
              onClick={submitMessage}
              disabled={!value.trim()}
              className="sm:hidden px-4 py-2 rounded-lg bg-accent-primary text-white text-sm font-medium disabled:opacity-40 cursor-pointer ml-auto"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
