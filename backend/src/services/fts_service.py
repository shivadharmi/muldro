"""Postgres Full-Text Search query service.

Uses tsvector columns already present on 7 core tables to provide
ranked keyword search scoped to a workspace. Each table is queried
independently (no UNION ALL) for simpler maintenance and type safety.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Table -> (id_col, title_expr, text_expr, result_type)
# title_expr / text_expr are SQL fragments interpolated into the query.
_TABLE_MAP: dict[str, dict[str, str]] = {
    "memories": {
        "id_col": "memory_id",
        "title_expr": "LEFT(fact_text, 80)",
        "text_expr": "fact_text",
        "result_type": "memory",
    },
    "entities": {
        "id_col": "entity_id",
        "title_expr": "canonical_name",
        "text_expr": "canonical_name || ' ' || entity_type",
        "result_type": "entity",
    },
    "normalized_events": {
        "id_col": "event_id",
        "title_expr": "title",
        "text_expr": "summary",
        "result_type": "event",
    },
    "conversations": {
        "id_col": "conversation_id",
        "title_expr": "title",
        "text_expr": "title",
        "result_type": "conversation",
    },
    "messages": {
        "id_col": "message_id",
        "title_expr": "LEFT(content, 80)",
        "text_expr": "content",
        "result_type": "message",
    },
    "briefings": {
        "id_col": "briefing_id",
        "title_expr": "headline",
        "text_expr": "full_text",
        "result_type": "briefing",
    },
    "approvals": {
        "id_col": "approval_id",
        "title_expr": "title",
        "text_expr": "summary",
        "result_type": "approval",
    },
}

_VALID_TABLES = frozenset(_TABLE_MAP.keys())


class FTSService:
    """Postgres full-text search across workspace-scoped tables."""

    def __init__(self, db: AsyncSession, workspace_id: str) -> None:
        self._db = db
        self._workspace_id = workspace_id

    async def search(
        self,
        query: str,
        tables: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search across multiple tables using tsvector ranking.

        Args:
            query: Natural-language search string (passed through
                ``plainto_tsquery`` for safe parsing).
            tables: Subset of tables to search. Defaults to all 7.
            limit: Maximum results **per table**.

        Returns:
            Merged list of result dicts sorted by score descending.
        """
        target_tables = [t for t in tables if t in _VALID_TABLES] if tables else list(_VALID_TABLES)

        all_results: list[dict] = []
        for table in target_tables:
            try:
                rows = await self.search_table(table, query, limit)
                all_results.extend(rows)
            except Exception:
                logger.warning("FTS query failed for table %s", table, exc_info=True)

        all_results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return all_results

    async def search_table(
        self,
        table: str,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        """Run a single-table FTS query.

        Args:
            table: One of the 7 supported table names.
            query: Search string.
            limit: Max rows to return.

        Returns:
            List of result dicts tagged with ``source_db``,
            ``result_type``, ``id``, ``title``, ``text``, ``score``.
        """
        if table not in _TABLE_MAP:
            logger.warning("FTS: unsupported table %s", table)
            return []

        meta = _TABLE_MAP[table]
        id_col = meta["id_col"]
        title_expr = meta["title_expr"]
        text_expr = meta["text_expr"]
        result_type = meta["result_type"]

        # NOTE: table name and column expressions come from the
        # trusted _TABLE_MAP constant — never from user input.
        sql = text(f"""
            SELECT
                {id_col}    AS id,
                {title_expr} AS title,
                {text_expr}  AS text_content,
                ts_rank(search_vector, query) AS score
            FROM {table},
                 plainto_tsquery('english', :query) AS query
            WHERE workspace_id = :workspace_id
              AND search_vector @@ query
            ORDER BY score DESC
            LIMIT :lim
        """)

        result = await self._db.execute(
            sql,
            {
                "query": query,
                "workspace_id": self._workspace_id,
                "lim": limit,
            },
        )

        return [
            {
                "id": row.id,
                "title": row.title or "",
                "text": row.text_content or "",
                "score": float(row.score),
                "source_db": "postgres_fts",
                "result_type": result_type,
            }
            for row in result.fetchall()
        ]
