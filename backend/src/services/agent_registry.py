"""AgentRegistry — DB-backed agent configuration with in-memory cache.

Replaces the hardcoded AGENTS dict with a persistent, mutable registry.
Agents are loaded from the DB at startup and cached in memory. Mutations
go through the DB and refresh the cache.
"""

import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.agents import Agent
from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES, AGENT_MODEL_TIERS, SubAgent
from src.orchestrator.prompts import AGENT_PROMPTS

logger = logging.getLogger(__name__)

# Display names for the 8 default agents
_DEFAULT_DISPLAY_NAMES = {
    "observer": "Observer",
    "librarian": "Librarian",
    "planner": "Planner",
    "governor": "Governor",
    "operator": "Operator",
    "presenter": "Presenter",
    "researcher": "Researcher",
    "persona": "Persona",
}

_DEFAULT_DESCRIPTIONS = {
    "observer": "Perceives the world — reads data sources, detects changes, ingests events.",
    "librarian": "Understands events — extracts entities, updates world model, curates memories.",
    "planner": "Decides what to do — produces structured task graphs, never prose.",
    "governor": "Enforces safety — evaluates policies, gates approvals, audits actions.",
    "operator": "Executes approved plans — calls external tools, tracks execution state.",
    "presenter": "Communicates with the user — briefings, notifications, dynamic UI.",
    "researcher": "Gathers deep context — cross-source synthesis, fact validation.",
    "persona": "Learns preferences — adapts communication style, detects patterns.",
}


class AgentRegistry:
    """DB-backed registry for agent configurations."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def seed_defaults(self) -> int:
        """Seed or update the 8 default agents. Returns count of agents created/updated.

        Creates new agents that don't exist. For existing agents, syncs
        capability_scope and system_prompt from hardcoded defaults so that
        code changes to AGENT_CAPABILITY_SCOPES or AGENT_PROMPTS are
        reflected without manual DB migration.
        """
        result = await self._db.execute(select(Agent))
        existing = {agent.name: agent for agent in result.scalars().all()}

        changed = 0
        for name, prompt in AGENT_PROMPTS.items():
            expected_scope = sorted(AGENT_CAPABILITY_SCOPES.get(name, set()))

            if name not in existing:
                agent = Agent(
                    agent_id=f"agt_{ULID()}",
                    name=name,
                    display_name=_DEFAULT_DISPLAY_NAMES.get(name, name.title()),
                    description=_DEFAULT_DESCRIPTIONS.get(name),
                    system_prompt=prompt,
                    model_tier=AGENT_MODEL_TIERS.get(name, "sonnet"),
                    capability_scope=expected_scope,
                    max_tokens=8192 if name == "planner" else 4096,
                    temperature=0.1 if name == "governor" else 0.3,
                    enabled=True,
                )
                self._db.add(agent)
                changed += 1
                continue

            # Sync capability_scope and system_prompt if they diverged
            agent = existing[name]
            needs_update = False

            if sorted(agent.capability_scope or []) != expected_scope:
                agent.capability_scope = expected_scope
                needs_update = True

            if agent.system_prompt != prompt:
                agent.system_prompt = prompt
                needs_update = True

            if needs_update:
                changed += 1

        if changed:
            await self._db.flush()
            logger.info("Seeded/updated %d agent definitions", changed)

        return changed

    async def list_agents(self, include_disabled: bool = False) -> list[Agent]:
        """List all agents, optionally including disabled ones."""
        stmt = select(Agent).order_by(Agent.name)
        if not include_disabled:
            stmt = stmt.where(Agent.enabled.is_(True))
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_agent(self, agent_id: str) -> Agent | None:
        """Get a single agent by ID."""
        result = await self._db.execute(select(Agent).where(Agent.agent_id == agent_id))
        return result.scalar_one_or_none()

    async def get_agent_by_name(self, name: str) -> Agent | None:
        """Get a single agent by name."""
        result = await self._db.execute(select(Agent).where(Agent.name == name))
        return result.scalar_one_or_none()

    async def create_agent(
        self,
        name: str,
        display_name: str,
        system_prompt: str,
        *,
        description: str | None = None,
        model_tier: str = "sonnet",
        capability_scope: list[str] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> Agent:
        """Create a new agent definition."""
        agent = Agent(
            agent_id=f"agt_{ULID()}",
            name=name,
            display_name=display_name,
            description=description,
            system_prompt=system_prompt,
            model_tier=model_tier,
            capability_scope=sorted(capability_scope or []),
            max_tokens=max_tokens,
            temperature=temperature,
            enabled=True,
        )
        self._db.add(agent)
        await self._db.flush()
        return agent

    async def update_agent(self, agent_id: str, updates: dict[str, Any]) -> Agent | None:
        """Update an agent's configuration. Returns the updated agent or None."""
        agent = await self.get_agent(agent_id)
        if not agent:
            return None

        allowed_fields = {
            "display_name",
            "description",
            "system_prompt",
            "model_tier",
            "capability_scope",
            "max_tokens",
            "temperature",
            "enabled",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_fields}

        if "capability_scope" in filtered and isinstance(filtered["capability_scope"], list):
            filtered["capability_scope"] = sorted(filtered["capability_scope"])

        if filtered:
            await self._db.execute(
                update(Agent).where(Agent.agent_id == agent_id).values(**filtered)
            )
            await self._db.flush()
            # Refresh to return updated state
            await self._db.refresh(agent)

        return agent

    async def toggle_agent(self, agent_id: str, enabled: bool) -> Agent | None:
        """Enable or disable an agent."""
        return await self.update_agent(agent_id, {"enabled": enabled})

    async def load_as_sub_agents(self) -> dict[str, SubAgent]:
        """Load all enabled agents as SubAgent instances (for the orchestrator)."""
        agents = await self.list_agents(include_disabled=False)
        result = {}
        for agent in agents:
            result[agent.name] = SubAgent(
                name=agent.name,
                prompt=agent.system_prompt,
                model_tier=agent.model_tier,
                capability_scope=set(agent.capability_scope) if agent.capability_scope else set(),
                max_tokens=agent.max_tokens,
                temperature=agent.temperature,
            )
        return result
