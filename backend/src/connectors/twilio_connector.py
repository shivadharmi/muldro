"""Twilio SMS connector — webhook ingestion + write actions (MCP fallback)."""

import logging
from datetime import datetime, timezone

from src.connectors.base import BaseConnector, ConnectorHealth, register_connector
from src.services.event_processor import RawEvent

logger = logging.getLogger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01"


@register_connector("sms")
class TwilioConnector(BaseConnector):
    """Twilio SMS connector. MCP server is the primary write path for sends."""

    supports_webhooks: bool = True
    supports_actions: bool = True
    available_actions: list[str] = ["send_sms"]

    def _account_sid(self) -> str:
        return getattr(self._settings, "twilio_account_sid", "") if self._settings else ""

    def _auth_token(self) -> str:
        return getattr(self._settings, "twilio_auth_token", "") if self._settings else ""

    def _from_number(self) -> str:
        return getattr(self._settings, "twilio_from_number", "") if self._settings else ""

    async def poll(
        self, user_id: str, cursor: str | None, credentials: dict
    ) -> tuple[list[RawEvent], str | None]:
        """Twilio is primarily webhook-based — no active polling."""
        return [], cursor

    async def test(self, credentials: dict) -> ConnectorHealth:
        import httpx

        sid = self._account_sid()
        token = self._auth_token()
        if not sid or not token:
            return ConnectorHealth(
                provider="sms",
                status="down",
                last_poll_at=None,
                error="Twilio credentials not configured",
            )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{TWILIO_API}/Accounts/{sid}.json",
                    auth=(sid, token),
                    timeout=10,
                )
                if resp.status_code == 200:
                    return ConnectorHealth(
                        provider="sms",
                        status="healthy",
                        last_poll_at=datetime.now(timezone.utc),
                    )
                return ConnectorHealth(
                    provider="sms",
                    status="down",
                    last_poll_at=None,
                    error=f"HTTP {resp.status_code}",
                )
        except Exception as e:
            return ConnectorHealth(provider="sms", status="down", last_poll_at=None, error=str(e))

    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        return ""  # No OAuth — basic auth

    async def handle_webhook(self, payload: dict) -> list[RawEvent]:
        """Parse incoming SMS from Twilio webhook (form data)."""
        from_number = payload.get("From", "")
        body = payload.get("Body", "")
        msg_sid = payload.get("MessageSid", "")

        if not from_number or not body:
            return []

        return [
            RawEvent(
                source="sms",
                source_account_id="twilio_primary",
                event_type="sms_received",
                entity_type="sms_thread",
                entity_id=from_number,
                occurred_at=datetime.now(timezone.utc),
                title=f"SMS from {from_number}",
                summary=body[:500],
                actor={"type": "person", "phone": from_number},
                raw_payload={"message_sid": msg_sid, "from": from_number},
            )
        ]

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        if action not in self.available_actions:
            return {"status": "error", "error": f"Unknown action: {action}"}
        return await self._action_send_sms(params)

    async def _action_send_sms(self, params: dict) -> dict:
        import httpx

        to = params.get("to", "")
        body = params.get("body", "")
        if not to or not body:
            return {"status": "error", "error": "to and body required"}

        sid = self._account_sid()
        token = self._auth_token()
        from_number = params.get("from", self._from_number())

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TWILIO_API}/Accounts/{sid}/Messages.json",
                data={"To": to, "From": from_number, "Body": body},
                auth=(sid, token),
                timeout=15,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {"status": "ok", "message_sid": data.get("sid")}
            return {"status": "error", "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
