/** Routes A2UI actions back to the Jarvis backend via WebSocket. */

export function handleA2UIAction(
  sendAction: (action: string, payload: Record<string, unknown>) => void,
  actionType: string,
  payload: Record<string, unknown>
) {
  // Map A2UI action types to WebSocket message format
  if (payload.action === "approve" || payload.action === "reject") {
    sendAction(payload.action as string, { id: payload.id });
  } else if (payload.action === "meeting_prep") {
    sendAction("meeting_prep", { event_id: payload.event_id });
  } else {
    // Generic action passthrough
    sendAction(actionType, payload);
  }
}
