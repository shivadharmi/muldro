"""SurfacePusher — builds and delivers A2UI workspace surfaces.

Extracted from ``JarvisOrchestrator`` (god-object decomposition, 2026-06-19).
Pushes Presenter surfaces, plan-derived workspace surfaces, and proactive insight
surfaces over Redis pub/sub (via ``EventPublisher``'s event bus) and persists them
to ``ui_surfaces``. Depends on ``EventPublisher`` and the db-factory provider.
"""

import json
import logging
import re
from typing import TYPE_CHECKING

from src.orchestrator.event_publisher import EventPublisher
from src.services.relevance_assessor import format_evidence
from src.services.surface_mapping import (
    build_surface_preview_from_plan,
    derive_surface_kind,
    extract_surface_data,
    strip_surface_blocks,
)

if TYPE_CHECKING:
    from src.contracts import PlanOutput
    from src.services.relevance_assessor import PerceptionSignal, RelevanceAssessment

logger = logging.getLogger(__name__)


# Per-event line emitted by ConnectorPoller.ingest_raw_events, e.g.
#   "- [gmail] email_received: INR 1087 spent ... (event_id=evt_01...)"
# We strip the "[source] event_type:" prefix and any trailing "(event_id=...)"
# / bare ULID so only the human subject remains.
_EVENT_PREFIX_RE = re.compile(r"^\s*[-*]?\s*\[[^\]]+\]\s*[\w.]+\s*:\s*")
_EVENT_ID_SUFFIX_RE = re.compile(r"\s*\(event_id=[^)]*\)\s*$")


def _clean_event_subject(line: str) -> str:
    """Strip the ``[source] event_type:`` prefix and ``(event_id=...)`` suffix.

    Returns the human-readable subject only. Empty string if nothing remains.
    """
    cleaned = _EVENT_PREFIX_RE.sub("", line)
    cleaned = _EVENT_ID_SUFFIX_RE.sub("", cleaned)
    return cleaned.strip()


def _clean_insight_title(raw_summary: str, *, max_len: int = 120) -> str:
    """Derive a clean, human insight-card title from a raw observer summary.

    The observer summary is agent-facing pipeline prose, e.g.::

        Polled gmail: 2 new event(s).
        - [gmail] email_received: INR 1087 spent ... (event_id=evt_01...)
        - [gmail] email_received: Lunch tomorrow? (event_id=evt_02...)

    This extracts the per-event subject lines, strips the ``[source] type:``
    prefix and ``(event_id=...)`` suffix, and builds a concise headline:

      - 0 subjects -> first non-"Polled" line, else "New activity"
      - 1 subject  -> that subject
      - N subjects -> "<N> new updates: <first subject>"

    The result is always truncated to ``max_len`` characters.
    """
    subjects: list[str] = []
    for raw_line in raw_summary.splitlines():
        line = raw_line.strip()
        if not line.startswith(("-", "*")):
            continue
        # Stop at the thread-context section — those are not new-event subjects.
        if line.startswith("---"):
            break
        subject = _clean_event_subject(line)
        if subject:
            subjects.append(subject)

    if not subjects:
        title = "New activity"
    elif len(subjects) == 1:
        title = subjects[0]
    else:
        title = f"{len(subjects)} new updates: {subjects[0]}"

    title = title.strip() or "New activity"
    if len(title) > max_len:
        title = title[: max_len - 1].rstrip() + "…"
    return title


def _build_action_preview(capability: str, description: str) -> str:
    """Generate tooltip preview text for an insight action based on capability type."""
    cap = capability.lower()
    if any(w in cap for w in ("send", "create", "update", "delete", "write")):
        return f"Creates a task to {description.lower()}"
    if any(w in cap for w in ("read", "search", "fetch", "list", "get")):
        return f"Fetches {capability.split('.')[-1]} data without taking action"
    if any(w in cap for w in ("respond", "reason", "summarize")):
        return f"Generates a response about {description.lower()}"
    return ""


class SurfacePusher:
    """Builds typed A2UI surfaces and delivers them via WebSocket + DB."""

    def __init__(self, events: EventPublisher, db_factory_provider):
        self._events = events
        self._db_factory_provider = db_factory_provider

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    async def check_surface_rate(self, user_id: str, surface_type: str) -> bool:
        """Return True if push is allowed under rate limit.

        Uses Redis INCR with TTL for a sliding window counter.
        Workspace: 5 per minute. Insight: 3 per 30 minutes.
        """
        event_bus = await self._events.ensure_event_bus()
        if not event_bus or not getattr(event_bus, "_redis", None):
            return True

        redis = event_bus._redis
        if surface_type == "insight":
            key = f"jarvis:surface_rate:insight:{user_id}"
            limit, window = 3, 1800
        else:
            key = f"jarvis:surface_rate:workspace:{user_id}"
            limit, window = 5, 60

        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
        return count <= limit

    async def push_presenter_surface(
        self,
        spec,
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> str | None:
        """Push a Presenter-specified surface to the workspace.

        Builds WorkspaceSurfacePush from a SurfaceSpec produced by the Presenter agent.
        """
        from datetime import datetime, timedelta, timezone

        from ulid import ULID

        from src.contracts import WorkspaceSurfacePush
        from src.ui.contracts import SurfaceMetric, SurfacePreview
        from src.ui.renderer import build_detail_config

        if not await self.check_surface_rate(user_id, "workspace"):
            logger.debug("Presenter surface rate-limited for user %s", user_id)
            return None

        try:
            event_bus = await self._events.ensure_event_bus()
            if not event_bus:
                return None

            surface_id = f"surf_{ULID()}"
            preview = SurfacePreview(
                title=spec.title,
                subtitle=spec.subtitle,
                status=spec.status,
                priority=spec.priority,
                metrics=[SurfaceMetric(**m) for m in spec.metrics] if spec.metrics else [],
                tags=spec.tags or [],
            )
            detail_config = build_detail_config(spec.kind, surface_id)

            # Extract typed surface_data before building the push so both the
            # WebSocket broadcast and the DB row carry the same payload.
            surface_data_payload = extract_surface_data(response_text)
            surface_data_dict = (
                surface_data_payload.model_dump(mode="json") if surface_data_payload else None
            )

            # Structural promotion gate — only push Presenter message
            # surfaces (kind=message) to the workspace feed when the
            # response carries at least one structural component or
            # multiple distinct sections. Plain-text replies stay
            # chat-only and return None here. Other kinds (briefing,
            # alert, etc.) always push because they are system
            # categorizations, not agent chat replies.
            if spec.kind == "message":
                from src.services.message_promotion import should_promote_to_workspace

                children = (
                    surface_data_payload.sections
                    if surface_data_payload and surface_data_payload.sections
                    else []
                )
                if not should_promote_to_workspace(children):
                    logger.debug(
                        "Presenter message surface not promoted — plain-text reply (user %s)",
                        user_id,
                    )
                    return None

            clean_preview = strip_surface_blocks(response_text) if response_text else ""

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind=spec.kind,
                preview=preview.model_dump(mode="json"),
                detail_config=(detail_config.model_dump(mode="json") if detail_config else None),
                source_run_id=run_id,
                response_preview=(clean_preview[:300] if clean_preview else None),
                created_at=datetime.now(timezone.utc).isoformat(),
                surface_data=surface_data_dict,
            )

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps({"type": "surface", "surface": surface.model_dump(mode="json")})
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to DB
            try:
                from src.models.ui_state import UISurface

                async with self._db_factory() as db:
                    payload = surface.model_dump(mode="json")
                    # Keep the persisted payload consistent with the WS shape;
                    # surface_data is already serialized on the model.
                    db.add(
                        UISurface(
                            surface_id=surface.id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type=spec.kind,
                            payload=payload,
                            preview=preview.model_dump(mode="json"),
                            detail_config=(
                                detail_config.model_dump(mode="json") if detail_config else None
                            ),
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to persist presenter surface", exc_info=True)

            return surface_id
        except Exception:
            logger.warning("Failed to push presenter surface", exc_info=True)
            return None

    async def push_workspace_surface(
        self,
        plan: "PlanOutput",
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> str | None:
        """Push a typed surface to the workspace via Redis Pub/Sub.

        Derives surface kind from plan step capabilities.
        Only pushes for plans with visual value beyond the chat response.
        Returns the generated surface_id on success, None otherwise.
        """
        from datetime import datetime, timedelta, timezone

        from src.contracts import WorkspaceSurfacePush
        from src.ui.renderer import build_detail_config

        mapping = derive_surface_kind(plan)
        if not mapping:
            return None

        if not await self.check_surface_rate(user_id, "workspace"):
            logger.debug("Surface push rate-limited for user %s", user_id)
            return None

        kind, default_title = mapping

        try:
            event_bus = await self._events.ensure_event_bus()
            if not event_bus:
                return

            from ulid import ULID

            surface_id = f"surf_{ULID()}"
            preview = build_surface_preview_from_plan(plan, kind, default_title, response_text)
            detail_config = build_detail_config(kind, surface_id)

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind=kind,
                preview=preview.model_dump(mode="json"),
                detail_config=(detail_config.model_dump(mode="json") if detail_config else None),
                decision=None,
                source_run_id=run_id,
                response_preview=(response_text[:300] if response_text else None),
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps(
                {
                    "type": "surface",
                    "surface": surface.model_dump(mode="json"),
                }
            )
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to ui_surfaces table so the workspace survives page refresh
            try:
                from src.models.ui_state import UISurface

                async with self._db_factory() as db:
                    db.add(
                        UISurface(
                            surface_id=surface.id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type=kind,
                            payload=surface.model_dump(mode="json"),
                            preview=preview.model_dump(mode="json"),
                            detail_config=(
                                detail_config.model_dump(mode="json") if detail_config else None
                            ),
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug(
                    "Failed to persist workspace surface to DB",
                    exc_info=True,
                )
            return surface_id
        except Exception:
            logger.warning("Failed to push workspace surface", exc_info=True)
            return None

    async def push_insight_surface(
        self,
        signal: "PerceptionSignal",
        assessment: "RelevanceAssessment",
        user_id: str,
        workspace_id: str,
    ) -> None:
        """Push a proactive insight surface to the workspace.

        Called when the relevance assessor routes a signal to the push tier.
        Creates a WorkspaceSurfacePush with kind='proactive_insight' and
        persists to ui_surfaces for workspace reconnection.
        """
        from datetime import datetime, timedelta, timezone

        from ulid import ULID

        from src.contracts import (
            InsightSurfaceData,
            SuggestedActionRef,
            WorkspaceSurfacePush,
        )
        from src.ui.contracts import SurfacePreview

        try:
            event_bus = await self._events.ensure_event_bus()
            if not event_bus:
                return

            if not await self.check_surface_rate(user_id, "insight"):
                logger.debug("Insight surface rate-limited for user %s", user_id)
                return

            surface_id = f"surf_{ULID()}"

            suggested_actions = [
                SuggestedActionRef(
                    description=a.description,
                    capability=a.capability,
                    action_input=a.action_input,
                    action_preview=_build_action_preview(a.capability, a.description),
                )
                for a in assessment.suggested_actions
            ]

            # Derive a clean, human headline from the raw agent-facing summary.
            # The raw observer summary ("Polled gmail: ... (event_id=...)") is
            # pipeline jargon and must never reach a user-facing surface.
            clean_title = _clean_insight_title(signal.summary)

            # Format the supporting-observation count into a human-readable
            # evidence line (e.g. "42 days observed"); None when unavailable.
            evidence = format_evidence(assessment.evidence_count, assessment.evidence_unit)

            insight_data = InsightSurfaceData(
                signal_source=signal.source,
                signal_category=signal.event_type,
                signal_summary=clean_title,
                relevance_score=assessment.relevance_score,
                relevance_reasoning=assessment.reasoning,
                related_goals=assessment.relates_to_goals,
                suggested_actions=suggested_actions,
                evidence=evidence,
            )

            preview = SurfacePreview(
                title=clean_title,
                subtitle=assessment.reasoning[:200] if assessment.reasoning else None,
                status="proposal",
                priority="high" if assessment.urgency == "immediate" else "medium",
                tags=[signal.source],
                evidence=insight_data.evidence,
            )

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind="proactive_insight",
                preview=preview.model_dump(mode="json"),
                detail_config=None,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            # Include insight data in the payload for the frontend
            surface_payload = surface.model_dump(mode="json")
            surface_payload["insight_data"] = insight_data.model_dump(mode="json")

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps({"type": "surface", "surface": surface_payload})
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to ui_surfaces
            try:
                from src.models.ui_state import UISurface

                async with self._db_factory() as db:
                    db.add(
                        UISurface(
                            surface_id=surface_id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type="proactive_insight",
                            payload=surface_payload,
                            preview=preview.model_dump(mode="json"),
                            detail_config=None,
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to persist insight surface to DB", exc_info=True)

        except Exception:
            logger.warning("Failed to push insight surface", exc_info=True)
