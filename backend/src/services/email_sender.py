"""Reusable AWS SES email service."""

import asyncio
import logging

logger = logging.getLogger(__name__)


class EmailSender:
    """Send emails via AWS SES (boto3, async via asyncio.to_thread)."""

    def __init__(self, settings) -> None:
        self._from_address = settings.ses_from_address
        self._region = settings.ses_region
        self._enabled = settings.ses_enabled

    async def send(
        self,
        to: str,
        subject: str,
        body_html: str = "",
        body_text: str = "",
    ) -> str:
        """Send an email via SES. Returns the SES MessageId."""
        if not self._enabled:
            raise RuntimeError("SES is not enabled (set JARVIS_SES_ENABLED=true)")
        if not self._from_address:
            raise RuntimeError("SES from address not configured (set JARVIS_SES_FROM_ADDRESS)")

        import boto3

        def _send() -> str:
            ses = boto3.client("ses", region_name=self._region)
            body: dict = {}
            if body_text:
                body["Text"] = {"Data": body_text, "Charset": "UTF-8"}
            if body_html:
                body["Html"] = {"Data": body_html, "Charset": "UTF-8"}
            resp = ses.send_email(
                Source=self._from_address,
                Destination={"ToAddresses": [to]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": body,
                },
            )
            return resp["MessageId"]

        message_id = await asyncio.to_thread(_send)
        logger.info("SES email sent to=%s message_id=%s", to, message_id)
        return message_id
