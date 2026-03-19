/** A2UI Protocol TypeScript types — matches backend src/ui/contracts.py */

export interface A2UIAction {
  type: "click" | "submit" | "change";
  payload: Record<string, unknown>;
}

export interface A2UIComponent {
  type: string; // Text, Button, Card, List, Row, Column, TextField, etc.
  id: string;
  properties: Record<string, unknown>;
  children: A2UIComponent[];
  actions: A2UIAction[];
}

export interface A2UISurface {
  type: "surface";
  id: string;
  children: A2UIComponent[];
  metadata: Record<string, unknown>;
}

/** WebSocket message types from Jarvis backend */
export type JarvisMessage =
  | { type: "surface"; surface: A2UISurface }
  | { type: "notification"; notification_id: string; notification_type: string; title: string; body: string; data: Record<string, unknown> }
  | { type: "notification_resolved"; notification_id: string; resolved_on: string }
  | { type: "action_result"; action: string; result: Record<string, unknown> }
  | { type: "heartbeat" }
  | { type: "auth_ok" }
  | { type: "auth_error"; message: string };
