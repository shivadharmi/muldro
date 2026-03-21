/** Command workspace state: active conversation, mode, scope, focused message. */

import { create } from "zustand";

export type CommandMode = "ask" | "plan" | "execute";
export type CommandScope = "general" | "workspace" | "entity" | "document";

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
}

export const useCommandStore = create<CommandState>((set) => ({
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
}));
