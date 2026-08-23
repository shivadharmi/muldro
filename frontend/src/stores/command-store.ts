/** Command workspace state: active conversation, mode, scope, focused message. */

import { create } from "zustand";

export type PermissionMode = "auto" | "ask" | "bypass";
export type CommandScope = "general" | "workspace" | "entity" | "document";

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

  // Permission mode & scope
  permissionMode: PermissionMode;
  setPermissionMode: (mode: PermissionMode) => void;
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
}

export const useCommandStore = create<CommandState>((set) => ({
  conversationId: null,
  setConversationId: (id) => set({ conversationId: id }),

  permissionMode: "auto",
  setPermissionMode: (permissionMode) => set({ permissionMode }),
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
}));
