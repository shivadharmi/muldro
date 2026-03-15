"""Sub-agent definitions for the Jarvis orchestrator.

Defines 8 specialized agents with their prompts, model assignments,
and tool access scopes. Uses hub-and-spoke topology — the orchestrator
routes to agents, agents never call each other directly.
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

# Tool access scopes per agent — defines which tools each agent can use
AGENT_TOOL_SCOPES: dict[str, set[str]] = {
    "observer": {
        # Read from external sources
        "gmail_list",
        "gmail_read",
        "gmail_search",
        "calendar_list",
        "calendar_get",
        "drive_list",
        "drive_search",
        "slack_list_channels",
        "slack_get_messages",
        "slack_search",
        # Internal: ingest events
        "ingest_event",
        "report_observation",
        # Cursors
        "get_observation_cursor",
        "update_observation_cursor",
    },
    "librarian": {
        "update_entity",
        "get_entities",
        "search_memory",
    },
    "planner": {
        "plan_command",
        "get_active_plans",
        "search_memory",
        "get_entities",
    },
    "governor": {
        "evaluate_policy",
        "approve_action",
    },
    "operator": {
        # External writes (Governor-gated)
        "gmail_send",
        "gmail_send_email",
        "gmail_draft",
        "gmail_create_draft",
        "gmail_reply",
        "calendar_create",
        "calendar_create_event",
        "calendar_update",
        "calendar_update_event",
        "slack_post_message",
        "slack_send_message",
        "github_comment",
        "github_create_issue",
        "github_create_pr",
        # Internal: execution tracking
        "update_execution",
    },
    "presenter": {
        "get_briefing",
        "search_memory",
        "get_entities",
        # Communication
        "send_telegram",
        "send_approval_prompt",
        "push_ui_update",
    },
    "researcher": {
        # Read-only from all sources
        "search_memory",
        "get_entities",
        "gmail_list",
        "gmail_read",
        "gmail_search",
        "calendar_list",
        "calendar_get",
        "drive_list",
        "drive_search",
        "slack_list_channels",
        "slack_get_messages",
        "slack_search",
        # Web research
        "perplexity_search",
        # Browser
        "playwright_navigate",
        "playwright_screenshot",
        "playwright_get_text",
    },
    "persona": {
        "search_memory",
        "extract_preferences",
    },
}


@dataclass
class SubAgent:
    """Definition of a Jarvis sub-agent."""

    name: str
    prompt: str
    model_tier: str  # opus, sonnet, haiku
    tool_scope: set[str] = field(default_factory=set)
    max_tokens: int = 4096
    temperature: float = 0.3

    def can_use_tool(self, tool_name: str) -> bool:
        """Check if this agent is allowed to use a specific tool."""
        return tool_name in self.tool_scope


def create_sub_agents() -> dict[str, SubAgent]:
    """Create all 8 sub-agent definitions."""
    agents = {}
    for name, prompt in AGENT_PROMPTS.items():
        agents[name] = SubAgent(
            name=name,
            prompt=prompt,
            model_tier=AGENT_MODEL_TIERS.get(name, "sonnet"),
            tool_scope=AGENT_TOOL_SCOPES.get(name, set()),
            max_tokens=8192 if name == "planner" else 4096,
            temperature=0.1 if name == "governor" else 0.3,
        )
    return agents


# Pre-built agent registry
AGENTS = create_sub_agents()
