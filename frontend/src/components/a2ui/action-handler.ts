/** Routes A2UI actions back to the Muldro backend via WebSocket.
 *
 * Simplified: always sends payload.action + full payload.
 * The backend ACTION_HANDLERS registry handles dispatch.
 */

export function handleA2UIAction(
  sendAction: (action: string, payload: Record<string, unknown>) => void,
  _actionType: string,
  payload: Record<string, unknown>
) {
  const action = (payload.action as string) || _actionType;
  sendAction(action, payload);
}
