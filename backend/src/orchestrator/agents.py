"""Sub-agent definitions for the Jarvis orchestrator.

Defines 8 specialized agents with their prompts, model assignments,
capability-based access scopes, and per-agent thinking configuration.
"""

from dataclasses import dataclass, field

from src.orchestrator.prompts import AGENT_PROMPTS

# Model tier assignments per agent
AGENT_MODEL_TIERS = {
    "observer": "sonnet",
    "librarian": "sonnet",
    "planner": "opus",
    "governor": "sonnet",
    "operator": "sonnet",
    "presenter": "sonnet",
    "researcher": "sonnet",
    "persona": "haiku",
}

# Capability-based scopes per agent — abstracts over tool names.
# If a tool's canonical capability is in an agent's capability scope,
# the agent is allowed to use that tool regardless of its raw name.
AGENT_CAPABILITY_SCOPES: dict[str, set[str]] = {
    "observer": {
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
        "workflow.list",
        "workflow.get",
        "workflow.search",
        "internal.ingest_event",
        "internal.report_observation",
        "internal.get_cursor",
        "internal.update_cursor",
    },
    "librarian": {
        "internal.update_entity",
        "internal.get_entities",
        "internal.search_memory",
    },
    "planner": {
        "internal.plan_command",
        "internal.get_plans",
        "internal.search_memory",
        "internal.get_entities",
    },
    "governor": {
        "internal.evaluate_policy",
        "internal.approve_action",
    },
    "operator": {
        "email.send",
        "email.draft",
        "email.reply",
        "calendar.create",
        "calendar.update",
        "messaging.send",
        "messaging.reply",
        "messaging.react",
        "messaging.update",
        "messaging.send_template",
        "messaging.post",
        "messaging.share",
        "issue.create",
        "issue.update",
        "issue.comment",
        "issue.transition",
        "issue.sub_issue",
        "repo.create_pr",
        "repo.merge_pr",
        "repo.update_pr",
        "workflow.create_issue",
        "workflow.update_issue",
        "workflow.transition",
        "workflow.comment",
        "doc.create",
        "doc.update",
        "doc.comment",
        "doc.append",
        "internal.update_execution",
    },
    "presenter": {
        "internal.get_briefing",
        "internal.search_memory",
        "internal.get_entities",
        "internal.send_telegram",
        "internal.send_approval",
        "internal.push_ui",
        "messaging.send",
    },
    "researcher": {
        "internal.search_memory",
        "internal.get_entities",
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
        "search.web",
        "browser.open",
        "browser.snapshot",
        "browser.extract",
        "browser.screenshot",
    },
    "persona": {
        "internal.search_memory",
        "internal.extract_preferences",
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
    "researcher": ThinkingConfig(enabled=True, budget_tokens=6144),
    "librarian": ThinkingConfig(enabled=True, budget_tokens=4096),
    "presenter": ThinkingConfig(enabled=True, budget_tokens=4096),
    "governor": ThinkingConfig(enabled=True, budget_tokens=2048),
    "operator": ThinkingConfig(enabled=True, budget_tokens=2048),
    "observer": ThinkingConfig(enabled=True, budget_tokens=2048),
    "persona": ThinkingConfig(enabled=True, budget_tokens=2048),
}


@dataclass
class SubAgent:
    """Definition of a Jarvis sub-agent."""

    name: str
    prompt: str
    model_tier: str  # opus, sonnet, haiku
    capability_scope: set[str] = field(default_factory=set)
    max_tokens: int = 4096
    temperature: float = 0.3
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)

    def can_use_tool(self, tool_name: str) -> bool:
        """Check if this agent is allowed to use a specific tool.

        Resolves the tool's canonical capability and checks against
        this agent's capability_scope. Handles namespaced MCP tool names
        (e.g. google_workspace_gmail_list → gmail_list).
        """
        if not self.capability_scope:
            return False
        from src.integrations.capabilities import get_capability_for_tool

        # Try direct match first
        cap = get_capability_for_tool(tool_name)
        if cap is not None:
            return cap in self.capability_scope

        # Normalize the tool name (handles camelCase, kebab-case, server prefixes)
        # and retry capability lookup on the canonical form
        from src.integrations.tool_normalizer import get_normalizer

        normalizer = get_normalizer()
        canonical = normalizer.normalize(tool_name)
        if canonical != tool_name:
            cap = get_capability_for_tool(canonical)
            if cap is not None:
                return cap in self.capability_scope

        return False


def create_sub_agents() -> dict[str, SubAgent]:
    """Create all 8 sub-agent definitions."""
    agents = {}
    for name, prompt in AGENT_PROMPTS.items():
        agents[name] = SubAgent(
            name=name,
            prompt=prompt,
            model_tier=AGENT_MODEL_TIERS.get(name, "sonnet"),
            capability_scope=set(AGENT_CAPABILITY_SCOPES.get(name, set())),
            max_tokens=8192 if name == "planner" else 4096,
            temperature=0.1 if name == "governor" else 0.3,
            thinking=AGENT_THINKING.get(name, ThinkingConfig()),
        )
    return agents


# Pre-built agent registry
AGENTS = create_sub_agents()
