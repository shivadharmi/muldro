import { create } from "zustand";

type ActionSender = (action: string, payload: Record<string, unknown>) => void;

interface WsActionState {
  sendAction: ActionSender;
  setSendAction: (fn: ActionSender) => void;
}

const noop: ActionSender = () => {};

export const useWsActionStore = create<WsActionState>((set) => ({
  sendAction: noop,
  setSendAction: (fn) => set({ sendAction: fn }),
}));

