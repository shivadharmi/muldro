/** Command workspace state: active conversation, mode, scope, focused message. */

import { create } from "zustand";

export type CommandMode = "ask" | "plan" | "execute";
export type CommandScope = "general" | "workspace" | "entity" | "document";

interface CommandHistoryEntry {
  command: string;
  mode: CommandMode;
  timestamp: number;
}

/** Serializable chat message snapshot for cross-route persistence. */
export interface ChatSnapshot {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  streaming?: boolean;
}

interface CommandState {
  // Active conversation
  conversationId: string | null;
  setConversationId: (id: string | null) => void;

  // Mode & scope
  mode: CommandMode;
  setMode: (mode: CommandMode) => void;
  scope: CommandScope;
  setScope: (scope: CommandScope) => void;

  // Focused message (for context sidebar)
  focusedMessageId: string | null;
  setFocusedMessageId: (id: string | null) => void;

  // Input state
  inputValue: string;
  setInputValue: (value: string) => void;

  // Pending command (set by launcher, consumed by chat panel)
  pendingCommand: string | null;
  setPendingCommand: (cmd: string | null) => void;

  // Chat message cache — survives navigation
  cachedMessages: ChatSnapshot[];
  setCachedMessages: (msgs: ChatSnapshot[]) => void;

  // Command history (last 20)
  history: CommandHistoryEntry[];
  addToHistory: (command: string) => void;
}

export const useCommandStore = create<CommandState>((set, get) => ({
  conversationId: null,
  setConversationId: (id) => set({ conversationId: id }),

  mode: "ask",
  setMode: (mode) => set({ mode }),
  scope: "general",
  setScope: (scope) => set({ scope }),

  focusedMessageId: null,
  setFocusedMessageId: (id) => set({ focusedMessageId: id }),

  inputValue: "",
  setInputValue: (value) => set({ inputValue: value }),

  pendingCommand: null,
  setPendingCommand: (cmd) => set({ pendingCommand: cmd }),

  cachedMessages: [],
  setCachedMessages: (msgs) => set({ cachedMessages: msgs }),

  history: [],
  addToHistory: (command) =>
    set({
      history: [
        { command, mode: get().mode, timestamp: Date.now() },
        ...get().history,
      ].slice(0, 20),
    }),
}));
