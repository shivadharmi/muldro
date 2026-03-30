"""Startup registry validation — catches tool registration inconsistencies early.

Runs after seeding, before accepting requests. All errors are collected
(not fail-on-first) and reported together. Can be skipped via
JARVIS_SKIP_REGISTRY_VALIDATION=true for emergencies.
"""

from __future__ import annotations


def validate_registry(
    internal_tools: list | None = None,
    external_seeds: list | None = None,
    capability_catalog: dict | None = None,
    agent_scopes: dict | None = None,
    tool_input_models: dict | None = None,
) -> list[str]:
    """Cross-validate registry consistency. Returns list of error messages (empty = valid).

    Checks:
    1. Tool capabilities reference known capabilities
    2. Agent scope capabilities exist in catalog
    3. Internal tools have non-null capabilities
    4. Critical-risk tools require approval
    5. Internal tools have schemas
    6. Read-only internal tools are low-risk
    """
    # Default to module-level constants if not provided
    if internal_tools is None:
        from src.tools.catalog import INTERNAL_TOOLS

        internal_tools = INTERNAL_TOOLS
    if external_seeds is None:
        from src.tools.catalog import EXTERNAL_TOOL_SEEDS

        external_seeds = EXTERNAL_TOOL_SEEDS
    if capability_catalog is None:
        from src.integrations.capabilities import CAPABILITY_CATALOG

        capability_catalog = CAPABILITY_CATALOG
    if agent_scopes is None:
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        agent_scopes = AGENT_CAPABILITY_SCOPES
    if tool_input_models is None:
        from src.tools.schemas import TOOL_INPUT_MODELS

        tool_input_models = TOOL_INPUT_MODELS

    errors: list[str] = []

    # Check 1: Tool capabilities reference known capabilities
    for tool in internal_tools:
        if tool.capability and tool.capability not in capability_catalog:
            errors.append(f"Tool '{tool.name}' references unknown capability '{tool.capability}'")

    for seed in external_seeds:
        if seed.capability and seed.capability not in capability_catalog:
            errors.append(f"Tool '{seed.name}' references unknown capability '{seed.capability}'")

    # Check 2: Agent scope capabilities exist in catalog
    for agent_name, scope_capabilities in agent_scopes.items():
        for cap in scope_capabilities:
            if cap not in capability_catalog:
                errors.append(f"Agent '{agent_name}' scope references unknown capability '{cap}'")

    # Check 3: Internal tools have non-null capabilities
    for tool in internal_tools:
        if not tool.capability:
            errors.append(f"Internal tool '{tool.name}' has no capability mapping")

    # Check 4: Critical-risk tools require approval
    for tool in internal_tools:
        if tool.risk_level == "critical" and not tool.requires_approval:
            errors.append(f"Critical tool '{tool.name}' does not require approval")

    for seed in external_seeds:
        if seed.risk_level == "critical" and not seed.requires_approval:
            errors.append(f"Critical tool '{seed.name}' does not require approval")

    # Check 5: Internal tools have schemas
    for tool in internal_tools:
        if tool.name not in tool_input_models:
            errors.append(f"Internal tool '{tool.name}' missing from TOOL_INPUT_MODELS")

    # Check 6: Read-only internal tools are low-risk
    for tool in internal_tools:
        if tool.read_only:
            if tool.risk_level != "low":
                errors.append(
                    f"Read-only tool '{tool.name}' has risk_level='{tool.risk_level}' "
                    "(expected 'low')"
                )
            if tool.requires_approval:
                errors.append(f"Read-only tool '{tool.name}' requires approval")

    return errors
