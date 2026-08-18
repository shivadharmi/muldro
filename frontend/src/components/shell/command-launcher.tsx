"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useShellStore } from "@/stores/shell-store";
import { useCommandStore, type PermissionMode } from "@/stores/command-store";

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

const MODES: { value: PermissionMode; label: string; icon: string }[] = [
  { value: "auto", label: "Auto", icon: "◐" },
  { value: "ask", label: "Ask", icon: "?" },
  { value: "bypass", label: "Bypass", icon: "▶" },
];

const SUGGESTIONS: { mode: PermissionMode; text: string }[] = [
  { mode: "auto", text: "Triage my inbox from this morning" },
  { mode: "ask", text: "Plan a weekly digest for my team" },
  { mode: "bypass", text: "Sync Linear issues to Notion" },
];

export function CommandLauncher() {
  const { commandLauncherOpen, closeCommandLauncher } = useShellStore();
  const { permissionMode, setPermissionMode } = useCommandStore();
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

  const submitText = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      // Set pending command in store, then navigate to chat
      setPendingCommand(trimmed);
      router.push("/chat");
      handleClose();
    },
    [router, handleClose, setPendingCommand]
  );

  const submitMessage = useCallback(() => {
    submitText(value);
  }, [value, submitText]);

  const selectSuggestion = useCallback(
    (suggestion: (typeof SUGGESTIONS)[number]) => {
      setPermissionMode(suggestion.mode);
      submitText(suggestion.text);
    },
    [setPermissionMode, submitText]
  );

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
      <div className="fixed z-50 inset-x-0 bottom-0 sm:bottom-auto sm:top-[18%] sm:left-1/2 sm:-translate-x-1/2 sm:w-full sm:max-w-[540px]">
        <div className="sm:mx-4 rounded-t-[var(--radius-xl)] sm:rounded-[var(--radius-xl)] bg-surface-1 border border-b-secondary shadow-[var(--shadow-lg)] overflow-hidden animate-scale-in">
          {/* Mode bar */}
          <div className="flex items-center gap-1 px-4 pt-3 pb-1">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => setPermissionMode(m.value)}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-full transition-all duration-150 cursor-pointer ${
                  permissionMode === m.value
                    ? "bg-j-primary text-j-primary-fg font-medium"
                    : "text-t-muted hover:text-t-secondary hover:bg-surface-2"
                }`}
              >
                <span>{m.icon}</span>
                <span>{m.label}</span>
              </button>
            ))}
            <div className="flex-1" />
            <kbd className="text-[10px] text-t-muted font-mono bg-surface-2 px-1.5 py-0.5 rounded-[var(--radius-sm)]">⌘K</kbd>
          </div>

          {/* Input — taller on mobile for touch targets */}
          <div className="px-4 py-3">
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
                permissionMode === "bypass"
                  ? "What should Muldro do?"
                  : "Ask Muldro anything…"
              }
              className="w-full bg-transparent text-base sm:text-[15px] text-t-primary placeholder-t-muted focus:outline-none"
              autoFocus
            />
          </div>

          {/* Empty-state suggestions — shown before the user types */}
          {!value && (
            <div className="border-t border-b-secondary">
              <div className="px-4 pt-3 pb-1 text-[10px] font-medium uppercase tracking-wide text-t-muted">
                Suggestions
              </div>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s.text}
                  type="button"
                  onClick={() => selectSuggestion(s)}
                  className="w-full text-left px-4 py-2.5 text-sm flex justify-between items-center gap-3 cursor-pointer text-t-primary hover:bg-surface-2 transition-colors group"
                >
                  <span className="truncate">{s.text}</span>
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 16 16"
                    fill="none"
                    className="shrink-0 text-t-muted group-hover:text-j-primary transition-colors"
                    aria-hidden="true"
                  >
                    <path
                      d="M4 8h8M8.5 4.5L12 8l-3.5 3.5"
                      stroke="currentColor"
                      strokeWidth="1.3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </button>
              ))}
            </div>
          )}

          {/* Slash command palette */}
          {isSlash && filtered.length > 0 && (
            <div className="border-t border-b-secondary max-h-64 overflow-y-auto">
              {filtered.map((cmd, i) => (
                <button
                  key={cmd.name}
                  type="button"
                  onClick={() => executeSlashCommand(cmd)}
                  className={`w-full text-left px-4 py-2.5 text-sm flex justify-between items-center cursor-pointer transition-colors ${
                    i === selectedIndex
                      ? "bg-j-primary-soft text-t-primary"
                      : "text-t-primary hover:bg-surface-2"
                  }`}
                >
                  <span className="font-mono text-j-primary text-[13px]">{cmd.name}</span>
                  <span className="text-t-muted text-xs">{cmd.description}</span>
                </button>
              ))}
            </div>
          )}

          {/* Footer — hints on desktop, send button on mobile */}
          <div className="px-4 py-2.5 border-t border-b-secondary flex items-center justify-between">
            <div className="hidden sm:flex items-center gap-4 text-[10px] text-t-muted">
              <span className="flex items-center gap-1"><kbd className="font-mono bg-surface-2 px-1 rounded-[var(--radius-sm)]">↵</kbd> Send</span>
              <span className="flex items-center gap-1"><kbd className="font-mono bg-surface-2 px-1 rounded-[var(--radius-sm)]">/</kbd> Commands</span>
              <span className="flex items-center gap-1"><kbd className="font-mono bg-surface-2 px-1 rounded-[var(--radius-sm)]">esc</kbd> Close</span>
            </div>
            <button
              type="button"
              onClick={submitMessage}
              disabled={!value.trim()}
              className="sm:hidden px-4 py-2 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg text-sm font-medium disabled:opacity-40 cursor-pointer ml-auto transition-colors"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
