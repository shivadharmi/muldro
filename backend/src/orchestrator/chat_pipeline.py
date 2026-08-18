"""Shared chat-orchestration helpers for MuldroOrchestrator (ORCH-P1-1).

``process_message`` (batch) and ``process_message_stream`` (SSE) are two
public entry points that drive the same intent→plan→route→execute→present
sequence with different I/O shells. The blocks that were *byte-identical*
between them — step-routing pre-resolution and the prompt/context string
builders — are centralized here so the two paths cannot silently drift (the
documented "wire it into BOTH" failure mode).

These are stateless functions, not a collaborator class: per
engineering-standards §2, prefer functions over a class with one method. The
orchestrator (a frozen god object) calls them instead of carrying the logic
inline. The two paths still own their own control flow and I/O — only the
duplicated, behavior-identical pieces live here.
"""

from __future__ import annotations

import json

from src.contracts import PlanStep
from src.services.capability_resolver import CapabilityResolver, route_step


async def resolve_plan_routing(
    db_factory,
    workspace_id: str,
    steps: list[PlanStep],
) -> tuple[list[tuple[PlanStep, str, list[dict]]], list[PlanStep]]:
    """Pre-resolve the agent + tools for each plan step.

    Returns ``(step_routing, user_steps)`` where ``step_routing`` is a list of
    ``(step, agent_name, tools)`` for agent-actor steps and ``user_steps`` are
    the steps the user must act on. Mirrors the resolution both chat paths run
    before executing steps.
    """
    step_routing: list[tuple[PlanStep, str, list[dict]]] = []
    user_steps: list[PlanStep] = []

    async with db_factory() as db:
        resolver = CapabilityResolver(db, workspace_id)
        for step in steps:
            if step.actor == "user":
                user_steps.append(step)
                continue
            if step.capability.startswith("system."):
                step_routing.append((step, "", []))
            elif step.capability in ("reason", "respond"):
                step_routing.append((step, "presenter", []))
            elif step.capability == "perceive":
                # Broad read: Perceiver gets ALL its tools, decides
                # autonomously which sources to query.
                step_routing.append((step, "perceiver", []))
            else:
                agent_name = await route_step(step.capability, resolver)
                tools = await resolver.resolve_for_step(step.capability)
                step_routing.append((step, agent_name, tools))
    return step_routing, user_steps


def build_user_action_block(user_steps: list[PlanStep]) -> str:
    """Render the 'User actions required' block from user-actor steps.

    Callers guard with ``if user_steps:`` before calling (the orchestrator also
    emits a structured ``user_actions`` payload alongside this text).
    """
    actions = "\n".join(
        f"- {s.description}" + (f" ({s.user_context})" if s.user_context else "")
        for s in user_steps
    )
    return f"\n\nUser actions required:\n{actions}"


def format_prior_step_results(outputs: dict) -> str:
    """Render the prior-step-results block injected into a downstream agent's
    message. Returns ``""`` when there are no outputs so callers can ``+=``
    unconditionally (matching the original ``if <outputs>:`` guard)."""
    if not outputs:
        return ""
    parts = []
    for key, output in outputs.items():
        parts.append(f"[{key}]:\n{str(output)}")
    return (
        "\n\n--- Prior step results ---\n"
        + "\n\n".join(parts)
        + "\n--- End of prior step results ---\n"
    )


def build_presenter_message(
    *,
    prompt_style: str,
    surface: str,
    message: str,
    intent: str,
    plan_dict: dict,
    plan_text: str,
    prior_results_block: str,
    user_action_block: str,
    history_block: str,
) -> str:
    """Build the Presenter prompt, preserving the two intentional styles
    (chat-pipeline-fold spec drift #1).

    ``prompt_style="conversational"`` (stream / live chat): "Respond to the
    user", leads with the intent. ``prompt_style="structured"`` (batch / WS
    surface-action callbacks + background scheduler one-shots): "Format this
    for the user", leads with the plan. Both share the prior-results,
    user-action, and history affordances. Callers pass ``""`` for any block
    they don't have (the empty-string builders above make ``+=`` a no-op).
    """
    if prompt_style == "structured":
        presenter_msg = (
            f"Format this for the user ({surface}). "
            f"Be conversational and helpful.\n\n"
            f"User message: {message}\n"
            f"Plan: {json.dumps(plan_dict)}"
        )
        if prior_results_block:
            presenter_msg += prior_results_block
        if plan_text:
            presenter_msg += f"\nAnalysis: {plan_text}"
    else:
        presenter_msg = (
            f"Respond to the user ({surface}). "
            f"Be conversational and helpful.\n\n"
            f"User message: {message}\n"
            f"Intent: {intent}\n"
        )
        if prior_results_block:
            presenter_msg += prior_results_block
        if plan_text:
            presenter_msg += f"Plan: {json.dumps(plan_dict)}\nAnalysis: {plan_text}\n"

    if user_action_block:
        presenter_msg += user_action_block
    if history_block:
        presenter_msg = f"{history_block}\n\n{presenter_msg}"
    return presenter_msg


def format_prior_results_for_presenter(outputs: dict) -> str:
    """Render the prior-step-results block for the Presenter prompt (its header
    instructs the Presenter to use them to answer). Returns ``""`` when empty."""
    if not outputs:
        return ""
    parts = []
    for agent_key, output in outputs.items():
        parts.append(f"[{agent_key}]:\n{str(output)}")
    return (
        "\n\n--- Prior step results (use these to answer the user) ---\n"
        + "\n\n".join(parts)
        + "\n--- End of prior step results ---\n"
    )
