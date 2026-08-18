"""Sub-agent definitions for the Muldro orchestrator.

Defines 6 specialized agents with their prompts, model assignments,
capability-based access scopes, and per-agent thinking configuration.
"""

from dataclasses import dataclass, field, replace

from src.orchestrator.prompts import AGENT_PROMPTS

# Model tier assignments per agent
AGENT_MODEL_TIERS = {
    "perceiver": "balanced",
    "librarian": "balanced",
    "planner": "reasoning",
    "executor": "balanced",
    "presenter": "balanced",
    "persona": "fast",
}

# Capability-based scopes per agent — abstracts over tool names.
# If a tool's canonical capability is in an agent's capability scope,
# the agent is allowed to use that tool regardless of its raw name.
AGENT_CAPABILITY_SCOPES: dict[str, set[str]] = {
    "perceiver": {
        # External data source reads (perception)
        "email.list",
        "email.read",
        "email.search",
        "calendar.list",
        "calendar.get",
        "doc.drive_list",
        "doc.drive_search",
        "doc.get",
        "doc.search",
        "doc.query",
        "messaging.list_channels",
        "messaging.get_history",
        "messaging.get_thread",
        "messaging.get_users",
        "messaging.get_profile",
        "messaging.search",
        "issue.list",
        "issue.get",
        "issue.search",
        "repo.search_code",
        "repo.search_repos",
        "repo.list_prs",
        "repo.get_diff",
        "repo.get_reviews",
        "workflow.list",
        "workflow.get",
        "workflow.search",
        "workflow.get_teams",
        # Internal observation tools
        "internal.ingest_event",
        "internal.report_observation",
        "internal.get_cursor",
        "internal.update_cursor",
        # Knowledge + web search
        "internal.search",
        "search.web",
        "browser.open",
        "browser.snapshot",
        "browser.extract",
        "browser.screenshot",
        # World-model reads (spec §4.6 item 5)
        "internal.get_entity",
        "internal.query_facts",
        "internal.traverse",
        "internal.get_provenance",
    },
    "librarian": {
        "internal.update_entity",
        "internal.search",
        "internal.store_memory",
        # World-model reads (spec §4.6 item 5)
        "internal.get_entity",
        "internal.query_facts",
        "internal.traverse",
        "internal.get_provenance",
    },
    "planner": {
        "internal.get_plans",
        "internal.get_goals",
        "internal.search",
        "internal.store_memory",
        "system.discovery",
        # World-model reads (spec §4.6 item 5)
        "internal.get_entity",
        "internal.query_facts",
        "internal.traverse",
        "internal.get_provenance",
    },
    "executor": {
        # Email
        "email.list",
        "email.read",
        "email.search",
        "email.send",
        "email.draft",
        "email.reply",
        # Calendar
        "calendar.list",
        "calendar.get",
        "calendar.create",
        "calendar.update",
        "calendar.delete",
        # Messaging
        "messaging.list_channels",
        "messaging.get_history",
        "messaging.get_thread",
        "messaging.send",
        "messaging.reply",
        "messaging.react",
        "messaging.update",
        "messaging.send_template",
        "messaging.post",
        "messaging.share",
        # Issues
        "issue.list",
        "issue.get",
        "issue.search",
        "issue.create",
        "issue.update",
        "issue.comment",
        "issue.transition",
        "issue.sub_issue",
        # Repos
        "repo.list_prs",
        "repo.get_diff",
        "repo.get_reviews",
        "repo.create_pr",
        "repo.merge_pr",
        "repo.update_pr",
        # Workflow
        "workflow.list",
        "workflow.get",
        "workflow.search",
        "workflow.create_issue",
        "workflow.update_issue",
        "workflow.transition",
        "workflow.comment",
        "workflow.delete",
        "workflow.delete_comment",
        "workflow.delete_milestone",
        # Docs
        "doc.create",
        "doc.update",
        "doc.comment",
        "doc.append",
        # Internal
        "internal.update_execution",
    },
    "presenter": {
        "internal.get_briefing",
        "internal.search",
        "internal.push_ui",
        "messaging.send",
    },
    "persona": {
        "internal.search",
        "internal.extract_preferences",
        "internal.store_preference",
    },
}


@dataclass
class ThinkingConfig:
    """Per-agent thinking configuration."""

    enabled: bool = True
    budget_tokens: int = 4096


# Per-agent thinking assignments
AGENT_THINKING: dict[str, ThinkingConfig] = {
    "planner": ThinkingConfig(enabled=True, budget_tokens=8192),
    "perceiver": ThinkingConfig(enabled=True, budget_tokens=6144),
    "librarian": ThinkingConfig(enabled=True, budget_tokens=4096),
    "presenter": ThinkingConfig(enabled=True, budget_tokens=4096),
    "executor": ThinkingConfig(enabled=True, budget_tokens=2048),
    "persona": ThinkingConfig(enabled=True, budget_tokens=2048),
}


@dataclass
class SubAgent:
    """Definition of a Muldro sub-agent."""

    name: str
    prompt: str
    model_tier: str  # reasoning, balanced, fast
    capability_scope: set[str] = field(default_factory=set)
    max_tokens: int = 4096
    temperature: float = 0.3
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)

    async def can_use_tool(self, tool_name: str, db, workspace_id: str | None = None) -> bool:
        """Registry-driven capability check. One lookup, no normalizer."""
        if not self.capability_scope:
            return False
        from src.services.tool_registry import ToolRegistry

        registry = ToolRegistry(db, workspace_id=workspace_id)
        tool = await registry.get_tool(tool_name)
        if tool and tool.capability:
            return tool.capability in self.capability_scope
        return False


def create_sub_agents() -> dict[str, SubAgent]:
    """Create all 6 sub-agent definitions."""
    agents = {}
    for name, prompt in AGENT_PROMPTS.items():
        agents[name] = SubAgent(
            name=name,
            prompt=prompt,
            model_tier=AGENT_MODEL_TIERS.get(name, "balanced"),
            capability_scope=set(AGENT_CAPABILITY_SCOPES.get(name, set())),
            max_tokens=8192 if name == "planner" else 4096,
            temperature=0.3,
            thinking=AGENT_THINKING.get(name, ThinkingConfig()),
        )
    return agents


def apply_cheap_mode(agent: SubAgent) -> SubAgent:
    """Return a cost-reduced copy of an agent for cheap mode.

    Cheap mode drops the reasoning tier to balanced (opus→sonnet): Opus is ~5x Sonnet
    and only the Planner uses it; balanced/fast tiers are left untouched. The resolver
    resolves the downgraded ``model_tier`` at build time, so this is the one lever that
    reaches the model.

    (Historical note: cheap mode also halved each agent's ``thinking.budget_tokens``, but
    the resolver-backed build path derives the thinking budget from the tier binding's
    effort — ``thinking.budget_tokens`` no longer reaches model construction — so that
    lever was inert and has been removed. Cheap-mode thinking reduction now follows from
    the tier downgrade's lower effort.)

    Returns a new SubAgent; the input is never mutated.
    """
    downgraded_tier = "balanced" if agent.model_tier == "reasoning" else agent.model_tier
    return replace(agent, model_tier=downgraded_tier)


def build_agent_set(base: dict[str, SubAgent], cheap_mode: bool) -> dict[str, SubAgent]:
    """Return the agent set to run with, applying cheap mode when enabled.

    Always returns a fresh dict so callers never alias the shared singleton.
    """
    if not cheap_mode:
        return dict(base)
    return {name: apply_cheap_mode(agent) for name, agent in base.items()}


# Pre-built agent registry
AGENTS = create_sub_agents()
