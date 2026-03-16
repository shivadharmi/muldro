"""Replay engine — re-execute recorded browser actions."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.browser.interfaces import ActionResult
from src.browser.playwright_session import (
    PlaywrightBrowserSession,
)
from src.models.browser_sessions import (
    BrowserAction as BrowserActionModel,
)

logger = logging.getLogger(__name__)


class ReplayEngine:
    """Reads browser_actions from the DB and replays them.

    Useful for automated regression runs or
    demonstrating a recorded workflow.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def load_actions(self, session_id: str) -> list[BrowserActionModel]:
        """Load ordered actions for a session."""
        stmt = (
            select(BrowserActionModel)
            .where(BrowserActionModel.session_id == session_id)
            .order_by(BrowserActionModel.created_at)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def replay(
        self,
        session_id: str,
        *,
        url_allowlist: list[str] | None = None,
        dry_run: bool = False,
    ) -> list[dict]:
        """Replay all actions for a recorded session.

        Args:
            session_id: The original session whose
                actions to replay.
            url_allowlist: Optional URL safety list.
            dry_run: If True, log actions without
                executing them.

        Returns:
            List of result dicts per action.
        """
        actions = await self.load_actions(session_id)
        if not actions:
            logger.warning(
                "No actions found for session %s",
                session_id,
            )
            return []

        results: list[dict] = []

        if dry_run:
            for act in actions:
                results.append(
                    {
                        "action_id": act.action_id,
                        "action_type": act.action_type,
                        "dry_run": True,
                        "selector": act.selector,
                        "value": act.value,
                    }
                )
            return results

        # Create a fresh session for replay
        replay_sess = PlaywrightBrowserSession(
            session_id=f"replay_{session_id[:48]}",
            user_id="system",
            url_allowlist=url_allowlist,
        )
        try:
            for act in actions:
                res = await self._execute_action(replay_sess, act)
                results.append(
                    {
                        "action_id": act.action_id,
                        "action_type": act.action_type,
                        "success": res.success,
                        "error": res.error,
                    }
                )
                if not res.success:
                    logger.warning(
                        "Replay stopped at action %s: %s",
                        act.action_id,
                        res.error,
                    )
                    break
        finally:
            await replay_sess.close()

        return results

    async def _execute_action(
        self,
        session: PlaywrightBrowserSession,
        action: BrowserActionModel,
    ) -> ActionResult:
        """Execute a single recorded action."""
        atype = action.action_type
        logger.info(
            "replay action=%s type=%s",
            action.action_id,
            atype,
        )

        if atype == "navigate" and action.value:
            page = await session.navigate(action.value)
            ok = page.status == "loaded"
            return ActionResult(
                success=ok,
                error=(None if ok else "Navigation failed"),
            )

        if atype == "click" and action.selector:
            return await session.click(action.selector)

        if atype == "fill" and action.selector:
            return await session.fill(
                action.selector,
                action.value or "",
            )

        if atype == "screenshot":
            data = await session.screenshot()
            return ActionResult(success=len(data) > 0)

        if atype == "extract":
            text = await session.extract_text(action.selector)
            return ActionResult(success=bool(text))

        return ActionResult(
            success=False,
            error=f"Unknown action type: {atype}",
        )
