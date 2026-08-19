"""Communication MCP server — user-facing delivery tools.

Built with FastMCP. Provides tools for pushing UI updates to the web
frontend via WebSocket/Redis pub-sub.
"""

import logging

from fastmcp import Context, FastMCP
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations

logger = logging.getLogger(__name__)

communication = FastMCP("muldro-communication")

_settings = None
_redis = None


def configure(settings, redis=None):
    """Configure with runtime dependencies."""
    global _settings, _redis
    _settings = settings
    _redis = redis


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

        channel = f"muldro:a2ui:{user_id}"

        # Check if payload is in new WorkspaceSurfacePush format (has preview + kind)
        if isinstance(parsed, dict) and "preview" in parsed and "kind" in parsed:
            message = json.dumps({"type": "surface", "surface": parsed})
        else:
            # Legacy A2UISurface format
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


@communication.tool(
    tags={"presenter", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
)
async def render_surface(
    kind: str,
    title: str,
    sections: list[dict],
    user_id: str,
    ctx: Context,
    subtitle: str | None = None,
    metrics: list[dict] | None = None,
) -> dict:
    """Render a rich workspace surface from typed A2UI components.

    The arguments have already been validated against `RenderSurfaceInput` by the tool layer,
    so an invalid component tree never reaches here — it is returned to the model as a tool
    error instead, which is the whole point of moving off the fenced-markdown channel.

    kind: Surface kind (message, summary, briefing, alert, recommendation)
    title: Surface title
    sections: The component tree to render, in display order
    user_id: User ID for the pub/sub channel
    subtitle: Optional subtitle
    metrics: Up to 4 headline numbers shown on the card preview
    """
    if not _redis:
        await ctx.info("Redis not available — skipping surface render")
        return {"status": "skipped", "reason": "redis_not_available"}

    try:
        import json

        from ulid import ULID

        surface_id = f"srf_{ULID()}"
        # `preview` + `kind` put this on push_ui_update's WorkspaceSurfacePush branch,
        # which is the shape the frontend already consumes.
        surface = {
            "surface_id": surface_id,
            "kind": kind,
            "preview": {
                "title": title,
                "subtitle": subtitle,
                "metrics": metrics or [],
            },
            "sections": sections,
        }

        channel = f"muldro:a2ui:{user_id}"
        await _redis.publish(channel, json.dumps({"type": "surface", "surface": surface}))
        return {"status": "published", "surface_id": surface_id, "channel": channel}
    except Exception as e:
        logger.error("render_surface failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}
