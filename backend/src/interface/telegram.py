"""Telegram bot interface for Jarvis.

Provides bidirectional communication: user sends messages, Jarvis responds.
Supports inline keyboard callbacks for approvals.
Registers with SurfaceRegistry for multi-surface coordination.
"""

import json
import logging
import time

from src.api.deps import resolve_workspace_id
from src.models.database import get_session_factory

logger = logging.getLogger(__name__)


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split a long message into chunks that fit Telegram's limit.

    Splits on paragraph boundaries first, then on line boundaries.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        # Try to split on double-newline (paragraph)
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            # Fall back to single newline
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            # Hard split
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


class TelegramRateLimiter:
    """Simple per-user sliding window rate limiter."""

    def __init__(self, max_per_minute: int = 10):
        self._max = max_per_minute
        self._windows: dict[str, tuple[int, float]] = {}

    def allow(self, user_id: str) -> bool:
        """Check if user is within rate limit. Returns True if allowed."""
        now = time.monotonic()
        count, window_start = self._windows.get(user_id, (0, now))
        if now - window_start > 60:
            count, window_start = 0, now
        count += 1
        self._windows[user_id] = (count, window_start)
        return count <= self._max


class TelegramInterface:
    """Manages the Telegram bot lifecycle and message handling."""

    def __init__(self, settings, orchestrator, surface_registry=None, notifier=None):
        self._settings = settings
        self._orchestrator = orchestrator
        self._surface_registry = surface_registry
        self._notifier = notifier
        self._app = None
        self._rate_limiter = TelegramRateLimiter()

    def _resolve_user_id(self) -> str:
        """Derive a stable user_id from the configured telegram_chat_id."""
        chat_id = self._settings.telegram_chat_id
        if not chat_id:
            raise ValueError("telegram_chat_id not configured — cannot resolve user_id")
        return f"usr_tg_{chat_id}"

    async def _resolve_workspace(self) -> str:
        """Resolve workspace_id for the Telegram user."""
        factory = get_session_factory()
        async with factory() as db:
            try:
                return await resolve_workspace_id(db, self._resolve_user_id())
            except ValueError:
                return ""

    async def start(self) -> None:
        """Start the Telegram bot (polling mode)."""
        if not self._settings.telegram_bot_token:
            logger.info("Telegram bot not configured (no JARVIS_TELEGRAM_BOT_TOKEN)")
            return

        try:
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError:
            logger.error("python-telegram-bot not installed")
            return

        self._app = Application.builder().token(self._settings.telegram_bot_token).build()

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("brief", self._handle_brief))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)

        # Register Telegram as an active surface
        if self._surface_registry:
            await self._surface_registry.register(
                self._resolve_user_id(),
                "telegram",
                metadata={"chat_id": self._settings.telegram_chat_id},
            )

        logger.info("Telegram bot started (polling)")

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._surface_registry:
            await self._surface_registry.unregister(self._resolve_user_id(), "telegram")

        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")

    async def send_message(
        self, text: str, parse_mode: str = "Markdown", reply_markup: str = ""
    ) -> dict:
        """Send a message via Telegram (callable by Notifier)."""
        if not self._settings.telegram_chat_id:
            return {"status": "skipped", "reason": "no_chat_id"}

        if not self._app or not self._app.bot:
            return await self._send_http(text, parse_mode, reply_markup)

        try:
            kwargs = {
                "chat_id": self._settings.telegram_chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }
            if reply_markup:
                kwargs["reply_markup"] = json.loads(reply_markup)

            msg = await self._app.bot.send_message(**kwargs)
            return {"status": "sent", "message_id": msg.message_id}
        except Exception as e:
            logger.error("send_message failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _handle_start(self, update, context) -> None:
        """Handle /start command."""
        await update.message.reply_text(
            "Jarvis is online. Send me any message to interact.\n\n"
            "Commands:\n"
            "/brief - Get your daily briefing\n"
            "/status - System status"
        )

    async def _handle_brief(self, update, context) -> None:
        """Handle /brief command — generate and send daily briefing."""
        await update.message.reply_text("Generating your briefing...")
        try:
            workspace_id = await self._resolve_workspace()
            result = await self._orchestrator.generate_briefing(
                user_id=self._resolve_user_id(), workspace_id=workspace_id
            )
            briefing_text = result.get("briefing", "No briefing available.")
            for chunk in _split_message(briefing_text):
                await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception as e:
            logger.error("Brief command failed: %s", e)
            await update.message.reply_text(f"Error generating briefing: {e}")

    async def _handle_status(self, update, context) -> None:
        """Handle /status command — show system status."""
        try:
            budget = await self._orchestrator.get_budget_status()

            surfaces = []
            if self._surface_registry:
                surfaces = await self._surface_registry.get_active_surfaces(self._resolve_user_id())

            text = (
                f"*Jarvis Status*\n"
                f"Budget: ${budget.daily_spend_usd:.2f} / "
                f"${budget.daily_limit_usd:.2f} "
                f"({budget.percent_used:.0f}%)\n"
                f"Mode: {budget.budget_mode}\n"
                f"Active surfaces: {', '.join(surfaces) or 'none'}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            logger.error("Status command failed: %s", e)
            await update.message.reply_text(f"Error: {e}")

    async def _handle_message(self, update, context) -> None:
        """Handle regular text messages — route through orchestrator."""
        text = update.message.text
        chat_id = str(update.message.chat_id)
        user_id = self._resolve_user_id()

        # Rate limiting check
        if not self._rate_limiter.allow(user_id):
            await update.message.reply_text("Slow down — I can handle 10 messages per minute.")
            return

        logger.info(
            "telegram_message_received",
            extra={"chat_id": chat_id, "text_length": len(text)},
        )

        try:
            workspace_id = await self._resolve_workspace()
            result = await self._orchestrator.process_message(
                message=text,
                user_id=user_id,
                workspace_id=workspace_id,
                surface="telegram",
                context={"chat_id": chat_id},
            )

            response = result.get("presentation") or result.get("summary", "")
            if not response:
                response = json.dumps(result, indent=2, default=str)

            for chunk in _split_message(response):
                await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception as e:
            logger.error("Message handling failed: %s", e)
            await update.message.reply_text(f"Error: {e}")

    async def _handle_callback(self, update, context) -> None:
        """Handle inline keyboard callbacks (approval buttons)."""
        query = update.callback_query
        await query.answer()

        data = query.data
        if not data:
            return

        try:
            if data.startswith("approve:") or data.startswith("reject:"):
                action, approval_id = data.split(":", 1)
                decision = "approved" if action == "approve" else "rejected"

                from src.tools.intelligence_server import approve_action

                result = await approve_action(
                    approval_id=approval_id,
                    decision=decision,
                    reason=f"{decision.title()} via Telegram",
                )
                status = result.get("status", "done")
                await query.edit_message_text(
                    f"{decision.title()}: {approval_id}\nStatus: {status}"
                )

                # Notify other surfaces that action was taken
                if self._notifier:
                    await self._notifier.on_action_taken(
                        self._resolve_user_id(), approval_id, "telegram"
                    )
        except Exception as e:
            logger.error("Callback handling failed: %s", e)
            await query.edit_message_text(f"Error: {e}")

    async def _send_http(self, text: str, parse_mode: str, reply_markup: str) -> dict:
        """Fallback: send via HTTP API when bot app isn't available."""
        if not self._settings.telegram_bot_token:
            return {"status": "skipped", "reason": "no_bot_token"}

        import httpx

        url = f"https://api.telegram.org/bot{self._settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self._settings.telegram_chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = json.loads(reply_markup)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, timeout=10)
                data = resp.json()
                if data.get("ok"):
                    return {
                        "status": "sent",
                        "message_id": data["result"]["message_id"],
                    }
                return {
                    "status": "error",
                    "error": data.get("description", "Unknown"),
                }
        except Exception as e:
            logger.error("Telegram HTTP fallback failed: %s", e)
            return {"status": "error", "error": str(e)}
