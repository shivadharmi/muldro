"""Communication MCP server — user-facing delivery tools.

Built with FastMCP. Provides tools for sending messages via Telegram,
pushing UI updates via WebSocket, and sending approval prompts.
"""

import logging

from fastmcp import Context, FastMCP
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations

logger = logging.getLogger(__name__)

communication = FastMCP("jarvis-communication")

_settings = None
_telegram_bot = None
_redis = None


def configure(settings, telegram_bot=None, redis=None):
    """Configure with runtime dependencies."""
    global _settings, _telegram_bot, _redis
    _settings = settings
    _telegram_bot = telegram_bot
    _redis = redis


@communication.tool(
    tags={"presenter", "write"},
    annotations=ToolAnnotations(destructiveHint=True, idempotentHint=False),
)
async def send_telegram(
    text: str,
    ctx: Context,
    parse_mode: str = "Markdown",
    reply_markup: str = "",
) -> dict:
    """Send a message to the user via Telegram.

    text: Message text (supports Markdown)
    parse_mode: Markdown or HTML
    reply_markup: JSON string of inline keyboard markup (optional)
    """
    if not _settings or not _settings.telegram_chat_id:
        await ctx.info("Telegram not configured — skipping send")
        return {"status": "skipped", "reason": "telegram_not_configured"}

    if not _telegram_bot:
        # Fallback: use httpx to call Telegram Bot API directly
        return await _send_telegram_http(text, parse_mode, reply_markup)

    try:
        kwargs = {
            "chat_id": _settings.telegram_chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            import json

            kwargs["reply_markup"] = json.loads(reply_markup)

        msg = await _telegram_bot.send_message(**kwargs)
        return {"status": "sent", "message_id": msg.message_id}
    except Exception as e:
        logger.error("send_telegram failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


@communication.tool(
    tags={"governor", "write"},
    annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True),
)
async def send_approval_prompt(
    approval_id: str,
    title: str,
    summary: str,
    ctx: Context,
    risk_level: str = "medium",
) -> dict:
    """Send an approval request with interactive Approve/Reject buttons via Telegram."""
    import json

    text = f"*Approval Required* ({risk_level})\n\n*{title}*\n{summary}"
    markup = json.dumps(
        {
            "inline_keyboard": [
                [
                    {"text": "Approve", "callback_data": f"approve:{approval_id}"},
                    {"text": "Reject", "callback_data": f"reject:{approval_id}"},
                ]
            ]
        }
    )
    return await send_telegram(text=text, parse_mode="Markdown", reply_markup=markup)


@communication.tool(
    tags={"presenter", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def push_ui_update(
    surface_id: str,
    payload: str,
    user_id: str,
    ctx: Context,
) -> dict:
    """Push a dynamic UI update to the web frontend via Redis pub/sub.

    surface_id: Identifier for the UI surface (e.g., 'daily_brief', 'approval_detail')
    payload: JSON string of the A2UI surface payload
    user_id: User ID for the pub/sub channel
    """
    if not _redis:
        await ctx.info("Redis not available — skipping UI push")
        return {"status": "skipped", "reason": "redis_not_available"}

    try:
        import json

        parsed = json.loads(payload) if isinstance(payload, str) else payload

        # Validate payload has expected A2UI shape
        from src.ui.contracts import A2UISurface

        try:
            A2UISurface.model_validate(parsed)
        except Exception:
            logger.warning("push_ui_update: payload is not a valid A2UISurface, sending as-is")

        channel = f"jarvis:a2ui:{user_id}"
        message = json.dumps(
            {
                "type": "surface_update",
                "surface_id": surface_id,
                "payload": parsed,
            }
        )
        await _redis.publish(channel, message)
        return {"status": "published", "channel": channel}
    except Exception as e:
        logger.error("push_ui_update failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


async def _send_telegram_http(text: str, parse_mode: str, reply_markup: str) -> dict:
    """Fallback: send Telegram message via HTTP API directly."""
    if not _settings or not _settings.telegram_bot_token:
        return {"status": "skipped", "reason": "no_bot_token"}

    import httpx

    url = f"https://api.telegram.org/bot{_settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": _settings.telegram_chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        import json

        payload["reply_markup"] = json.loads(reply_markup)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10)
            data = resp.json()
            if data.get("ok"):
                return {"status": "sent", "message_id": data["result"]["message_id"]}
            return {"status": "error", "error": data.get("description", "Unknown error")}
    except Exception as e:
        logger.error("Telegram HTTP fallback failed: %s", e)
        return {"status": "error", "error": str(e)}
