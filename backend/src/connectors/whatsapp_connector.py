"""WhatsApp connector — webhook-based ingestion + native write actions."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

WHATSAPP_API = "https://graph.facebook.com/v18.0"


@register_connector("whatsapp")
class WhatsAppConnector(BaseConnector):
    """WhatsApp Business API connector. No MCP server — native only."""

    cursor_type: str = "webhook_only"
    supports_webhooks: bool = True
    supports_actions: bool = True
    available_actions: list[str] = ["send_message", "send_template", "mark_read"]

    def _phone_id(self) -> str:
        return getattr(self._settings, "whatsapp_phone_number_id", "") if self._settings else ""

    def _access_token(self) -> str:
        return getattr(self._settings, "whatsapp_access_token", "") if self._settings else ""

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """WhatsApp is webhook-only — no polling."""
        return [], cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        import httpx

        token = self._access_token()
        phone_id = self._phone_id()
        if not token or not phone_id:
            return ConnectorHealth(
                provider="whatsapp",
                status="down",
                last_poll_at=None,
                error="WhatsApp credentials not configured",
            )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{WHATSAPP_API}/{phone_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return ConnectorHealth(
                        provider="whatsapp",
                        status="healthy",
                        last_poll_at=datetime.now(timezone.utc),
                    )
                return ConnectorHealth(
                    provider="whatsapp",
                    status="down",
                    last_poll_at=None,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(
                provider="whatsapp", status="down", last_poll_at=None, error=str(e)
            )

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return ""  # No OAuth — static token

    async def handle_webhook(self, payload: dict) -> list[RawEvent]:
        """Parse incoming WhatsApp webhook payload into RawEvents."""
        events: list[RawEvent] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    event = self._normalize_message(msg, value.get("contacts", []))
                    if event:
                        events.append(event)
        return events

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}

        dispatch = {
            "send_message": self._action_send_message,
            "send_template": self._action_send_template,
            "mark_read": self._action_mark_read,
        }
        return await dispatch[action](params)

    async def _action_send_message(self, params: dict) -> dict:
        import httpx

        to = params.get("to", "")
        text = params.get("text", "")
        if not to or not text:
            return {"status": "error", "error": "to and text required"}

        token = self._access_token()
        phone_id = self._phone_id()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{WHATSAPP_API}/{phone_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": text},
                },
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                msg_id = data.get("messages", [{}])[0].get("id", "")
                return {"status": "ok", "message_id": msg_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_send_template(self, params: dict) -> dict:
        import httpx

        to = params.get("to", "")
        template_name = params.get("template_name", "")
        language = params.get("language", "en")
        if not to or not template_name:
            return {"status": "error", "error": "to and template_name required"}

        token = self._access_token()
        phone_id = self._phone_id()

        body: dict = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        if params.get("components"):
            body["template"]["components"] = params["components"]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{WHATSAPP_API}/{phone_id}/messages",
                json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                msg_id = data.get("messages", [{}])[0].get("id", "")
                return {"status": "ok", "message_id": msg_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    async def _action_mark_read(self, params: dict) -> dict:
        import httpx

        message_id = params.get("message_id", "")
        if not message_id:
            return {"status": "error", "error": "message_id required"}

        token = self._access_token()
        phone_id = self._phone_id()

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{WHATSAPP_API}/{phone_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                },
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                return {"status": "ok", "message_id": message_id}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}

    @staticmethod
    def _normalize_message(msg: dict, contacts: list) -> RawEvent | None:
        msg_type = msg.get("type", "text")
        from_number = msg.get("from", "")
        msg_id = msg.get("id", "")
        timestamp = msg.get("timestamp", "")

        text = ""
        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
        elif msg_type == "image":
            text = "[Image message]"
        elif msg_type == "document":
            text = "[Document]"
        else:
            text = f"[{msg_type} message]"

        contact_name = ""
        for c in contacts:
            if c.get("wa_id") == from_number:
                contact_name = c.get("profile", {}).get("name", from_number)
                break

        occurred_at = None
        if timestamp:
            try:
                occurred_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            except (ValueError, OSError):
                pass

        return RawEvent(
            source="whatsapp",
            source_account_id="whatsapp_primary",
            event_type="message_received",
            entity_type="message_thread",
            entity_id=from_number,
            occurred_at=occurred_at,
            title=f"WhatsApp from {contact_name or from_number}",
            summary=text[:500],
            actor={"type": "person", "name": contact_name, "phone": from_number},
            raw_payload={"message_id": msg_id, "type": msg_type},
        )
