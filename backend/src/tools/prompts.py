"""MCP Prompt templates — reusable parameterized prompts for sub-agents.

Registered on the intelligence server so they're discoverable via MCP protocol.
Claude sub-agents can use ``get_prompt("plan_execution", {...})`` instead of
having prompt templates hardcoded in their system messages.
"""

from src.tools.intelligence_server import intelligence


@intelligence.prompt
def plan_execution(command: str, context: str = "") -> str:
    """Generate a structured task graph from a user command."""
    parts = [
        "Analyze the following command and produce a structured task graph.",
        "Return JSON with: decision, priority, risk_level, and tasks array.",
        "Each task must have: task_type, description, dependencies.",
        "",
        f"Command: {command}",
    ]
    if context:
        parts.append(f"Context: {context}")
    return "\n".join(parts)


@intelligence.prompt
def policy_evaluation(plan_summary: str, risk_level: str = "medium") -> str:
    """Evaluate governance policy for a plan."""
    return (
        f"Evaluate the following plan against governance policies.\n"
        f"Risk level: {risk_level}\n"
        f"Plan: {plan_summary}\n\n"
        f"Return one of: auto_execute, approval_required, blocked — with reasoning."
    )


@intelligence.prompt
def entity_extraction(event_summary: str, source: str = "unknown") -> str:
    """Extract entities (people, organizations, projects) from an event."""
    return (
        f"Extract all entities from the following event.\n"
        f"Source: {source}\n"
        f"Event: {event_summary}\n\n"
        f"For each entity return: name, type (person/organization/project), "
        f"and any attributes mentioned."
    )


@intelligence.prompt
def briefing_generation(
    date: str = "today",
    event_count: int = 0,
    pending_approvals: int = 0,
) -> str:
    """Generate a daily briefing for the user."""
    return (
        f"Generate a concise daily briefing for {date}.\n"
        f"Events to summarize: {event_count}\n"
        f"Pending approvals: {pending_approvals}\n\n"
        f"Structure: headline, top priorities, changes since last briefing, "
        f"pending actions, recommended next steps."
    )


@intelligence.prompt
def preference_extraction(interaction_text: str) -> str:
    """Extract user preferences from an interaction."""
    return (
        f"Analyze this interaction and extract any user preferences.\n"
        f"Interaction: {interaction_text}\n\n"
        f"Preferences can be about: communication style, timing, priorities, "
        f"tool preferences, or workflow habits. Return as structured list."
    )
