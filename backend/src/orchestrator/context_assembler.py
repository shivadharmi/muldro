"""ContextAssembler — builds ambient context blocks for agent prompts.

Extracted from ``JarvisOrchestrator`` (god-object decomposition, 2026-06-19).
A leaf collaborator: it loads conversation history (summarizing overflow via
Haiku), connected-integration identities, and a ``ContextPack`` from the
``ContextBuilder``, returning prompt-ready text. Depends only on settings, the
Anthropic client, the service container, and the DB session factory.
"""

import logging

from src.config.models import BEDROCK_MODEL_TIERS, MODEL_TIERS
from src.config.settings import Settings
from src.orchestrator.services import ServiceContainer
from src.services.context_builder import ContextBuilder, ContextPack

logger = logging.getLogger(__name__)


# Agents that benefit from context enrichment (read-heavy agents)
CONTEXT_ENRICHED_AGENTS = {
    "planner",
    "presenter",
    "perceiver",
    "librarian",
    "executor",
    "governor",
}


class ContextAssembler:
    """Assembles conversation history and ambient context for agent prompts."""

    def __init__(
        self,
        settings: Settings,
        services: ServiceContainer | None,
        db_factory_provider,
        client,
    ):
        self._settings = settings
        self._services = services
        # Provider (not a captured value) so reassigning db_factory on the
        # orchestrator propagates to this collaborator (see EventPublisher).
        self._db_factory_provider = db_factory_provider
        self._client = client

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    def _request_services(self, db) -> ServiceContainer:
        """Return a ServiceContainer whose DB-bound services use ``db``."""
        from src.runtime import request_services

        return request_services(self._services, self._settings, db)

    async def load_conversation_history(
        self,
        conversation_id: str | None,
        max_messages: int = 20,
        max_chars: int = 20000,
        user_id: str = "",
    ) -> str:
        """Load recent conversation history from DB for multi-turn context.

        Returns a formatted block of prior messages or empty string.
        Truncates to stay within token budget.
        """
        if not conversation_id or not self._db_factory:
            return ""

        try:
            from sqlalchemy import select

            from src.models.conversations import Message

            async with self._db_factory() as db:
                result = await db.execute(
                    select(Message.role, Message.content, Message.metadata_)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(max_messages + 1)  # +1 for the just-saved user message
                )
                rows = result.all()

            if len(rows) <= 1:
                # Only the current message — no history
                return ""

            # Reverse to chronological, skip the last (current) user message
            history = list(reversed(rows[1:]))

            lines: list[str] = []
            total = 0
            for role, content, meta in history:
                label = "User" if role == "user" else "Assistant"
                snippet = content
                # B4: Annotate with decision type for execution context
                decision_tag = ""
                if meta and isinstance(meta, dict):
                    decision_data = meta.get("decision")
                    if isinstance(decision_data, dict):
                        decision_tag = f" [{decision_data.get('decision', '')}]"
                line = f"{label}{decision_tag}: {snippet}"
                lines.append(line)
                total += len(line)

            if not lines:
                return ""

            # If history exceeds budget, summarize older messages
            if total > max_chars and len(lines) > 5:
                recent = lines[-5:]
                older = lines[:-5]
                summary = await self._summarize_history(
                    older, conversation_id=conversation_id, user_id=user_id
                )
                lines = [f"[Earlier conversation summary]: {summary}"] + recent

            # Final trim to budget
            output_lines: list[str] = []
            remaining = max_chars
            for line in lines:
                if remaining - len(line) < 0:
                    break
                output_lines.append(line)
                remaining -= len(line)

            if not output_lines:
                return ""

            return (
                "--- CONVERSATION HISTORY (most recent messages) ---\n"
                + "\n".join(output_lines)
                + "\n--- END HISTORY ---"
            )
        except Exception:
            logger.debug("Failed to load conversation history", exc_info=True)
            return ""

    async def _summarize_history(
        self, lines: list[str], conversation_id: str | None = None, user_id: str = ""
    ) -> str:
        """Summarize older conversation messages using Haiku (cheap, fast)."""
        try:
            if self._settings.use_bedrock:
                model = BEDROCK_MODEL_TIERS["haiku"]
            else:
                model = MODEL_TIERS["haiku"]

            text = "\n".join(lines)
            response = await self._client.messages.create(
                model=model,
                max_tokens=300,
                temperature=0,
                system=[
                    {
                        "type": "text",
                        "text": (
                            "Summarize this conversation in 2-3 sentences. "
                            "Focus on: topics discussed, decisions made, "
                            "and any pending items."
                        ),
                    }
                ],
                messages=[{"role": "user", "content": text}],
            )
            summary = "".join(b.text for b in response.content if b.type == "text")

            # Embed conversation summary into Qdrant for semantic search
            if summary and conversation_id:
                try:
                    from datetime import datetime, timezone

                    from src.services.embedding_service import EmbeddingService
                    from src.services.vector_store import VectorStore

                    if self._settings.qdrant_url:
                        vs = VectorStore(self._settings)
                        es = EmbeddingService(self._settings)
                        embedding = await es.embed_text(summary)
                        if embedding:
                            await vs.upsert(
                                collection="conversations",
                                id=conversation_id,
                                vector=embedding,
                                payload={
                                    "conversation_id": conversation_id,
                                    "workspace_id": "",
                                    "message_count": len(lines),
                                    "summary": summary,
                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                },
                                user_id=user_id,
                            )
                except Exception:
                    logger.debug(
                        "Conversation embedding failed for %s",
                        conversation_id,
                        exc_info=True,
                    )

            return summary
        except Exception:
            logger.debug("History summarization failed", exc_info=True)
            # Fallback: just truncate
            return "\n".join(lines)

    async def assemble_context(
        self, agent_name: str, message: str, user_id: str, workspace_id: str = ""
    ) -> str:
        """Pre-load relevant context for context-enriched agents using ContextBuilder.

        Returns a context block to append to the system prompt, giving the
        agent ambient awareness of the user's world without requiring it to
        explicitly call search_memory.
        """
        if agent_name not in CONTEXT_ENRICHED_AGENTS:
            return ""

        sections: list[str] = []

        # Load integration identity context (GitHub username, Google email, etc.)
        integration_ctx = await self._load_integration_context(user_id, workspace_id)
        if integration_ctx:
            sections.append(integration_ctx)

        try:
            async with self._db_factory() as db:
                svc = self._request_services(db)
                builder = ContextBuilder(
                    world_model=svc.world_model,
                    memory_service=svc.memory_service,
                    artifact_store=svc.artifact_store,
                    db=db,
                    graph_engine=svc.graph_engine,
                    tri_search=svc.tri_search,
                    reranker=svc.reranker,
                )
                pack: ContextPack = await builder.build(
                    user_id=user_id,
                    query=message,
                    workspace_id=workspace_id,
                )
                context_text = ContextBuilder.to_prompt(pack)
                if context_text:
                    sections.append(context_text)
        except Exception:
            logger.debug("Context assembly via ContextBuilder failed", exc_info=True)

        if sections:
            return "\n\n--- CONTEXT ---\n" + "\n\n".join(sections)
        return ""

    async def _load_integration_context(self, user_id: str, workspace_id: str) -> str:
        """Load connected integration identities for agent context.

        Returns a compact text block with provider-specific identity info
        (e.g., GitHub username/orgs, Google email) so agents can fill in
        required tool parameters like 'owner'.
        """
        try:
            from sqlalchemy import select

            from src.models.integration_installation import IntegrationInstallation

            async with self._db_factory() as db:
                result = await db.execute(
                    select(IntegrationInstallation).where(
                        IntegrationInstallation.workspace_id == workspace_id,
                        IntegrationInstallation.status == "active",
                        IntegrationInstallation.enabled.is_(True),
                        IntegrationInstallation.config.isnot(None),
                    )
                )
                installations = result.scalars().all()

            lines: list[str] = []
            for inst in installations:
                config = inst.config or {}
                if not config:
                    continue

                if inst.server_name == "github" and config.get("username"):
                    line = f"- GitHub: username={config['username']}"
                    if config.get("organizations"):
                        line += f", orgs=[{', '.join(config['organizations'])}]"
                    lines.append(line)
                elif config.get("account_email"):
                    label = inst.display_name or inst.server_name
                    lines.append(f"- {label}: {config['account_email']}")

            if lines:
                return "Connected integrations (use these for tool parameters):\n" + "\n".join(
                    lines
                )
        except Exception:
            logger.debug("Integration context load failed", exc_info=True)
        return ""
