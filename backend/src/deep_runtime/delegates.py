"""Read-only Jarvis research delegate for the deep runtime (Step 7B2 P2).

DORMANT scaffolding. Builds a Perceiver-as-delegate as a deepagents
``CompiledSubAgent`` — a LEAF compiled graph whose OWN capability-scope guard and
central tool dispatcher are baked into its compiled graph (build method A from the
Phase-0 spike; the gate lives inside the child, not on the lead). No live
lead->delegate routing exists yet; that is Step 8/10. Nothing here is wired into
the live streaming seam.

Two invariants this module locks:

1. The delegate is READ-ONLY. It carries the Perceiver's role prompt and the
   Perceiver's ``capability_scope`` (all reads, zero external-write capabilities).
   Its capability-scope guard — installed by ``build_deep_agent`` because a
   ``db_factory`` is passed — denies any out-of-scope tool call at runtime. There
   is deliberately NO ``trust_gate`` / ``write_lock`` middleware: those gate
   *writes*, and a delegate never writes.

2. The delegate NEVER carries the deep lead's Presenter-voice inline-format
   augmentation (``_augment_system_blocks_for_inline``). Its system prompt is its
   OWN role prompt (``PERCEIVER_PROMPT`` by default). The Presenter voice is a
   lead-only concern; a delegate returns structured findings, not user-facing prose.

``DELEGATE_RESPONSE_FORMAT`` (``DelegateSummary``) is the DOCUMENTED summary
contract, mirroring the Perceiver's ``<output_format>`` JSON (findings / synthesis
/ gaps / confidence). It is the shape the Phase-5 critique best-effort parses out
of the delegate's free-text ``task`` tool_result. It is deliberately NOT threaded
into ``create_deep_agent(response_format=...)`` as native structured output:
verified offline that ``create_deep_agent`` with a pydantic ``response_format``
raises ``StructuredOutputValidationError`` when the model's final message is not
valid JSON — too fragile. The delegate's structured shape comes from its PROMPT
(the Perceiver prompt already instructs findings/synthesis/gaps), and the summary
rides back to the lead as the ``task`` tool_result free-text.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from pydantic import BaseModel

from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.middleware.jarvis_tool_dispatcher import (
    ExecuteToolFn,
    make_jarvis_tool_dispatcher,
)
from src.deep_runtime.tool_bridge import build_tool_shells
from src.orchestrator.agents import SubAgent


class DelegateSummary(BaseModel):
    """Documented summary contract for a read-only delegate's result.

    Mirrors the Perceiver's ``<output_format>`` JSON (see ``PERCEIVER_PROMPT``):
    ``findings`` / ``synthesis`` / ``gaps`` (+ optional ``confidence``). This is
    the shape the Phase-5 critique best-effort parses out of the delegate's
    free-text ``task`` tool_result.

    It is NOT wired into ``create_deep_agent`` as native structured output:
    ``create_deep_agent(response_format=<pydantic>)`` raises
    ``StructuredOutputValidationError`` when the model's final message is not valid
    JSON (verified offline). The structured shape is produced by the delegate's
    PROMPT instead. See the module docstring for the full rationale.
    """

    findings: list[str] = []
    synthesis: str = ""
    gaps: list[str] = []
    confidence: float | None = None


# Public alias: the documented delegate summary contract.
DELEGATE_RESPONSE_FORMAT = DelegateSummary


async def build_read_only_delegate(
    agent_config: SubAgent,
    tools: list[dict[str, Any]],
    *,
    workspace_id: str,
    user_id: str,
    db_factory,
    execute_tool: ExecuteToolFn,
    system_prompt: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Build a read-only Jarvis delegate as a deepagents ``CompiledSubAgent`` dict.

    The returned ``{"name", "description", "runnable"}`` dict is registered on a deep
    lead via ``create_deep_agent(subagents=[...])`` / ``build_deep_agent(subagents=...)``
    so the lead's built-in ``task`` tool can route to it. The gate is baked into the
    child's own compiled graph:

    - ``build_tool_shells`` turns the Jarvis tool defs into inert schema shells (the
      model SEES each tool but never runs its body);
    - ``make_jarvis_tool_dispatcher`` centralizes execution through ``execute_tool``;
    - ``build_deep_agent`` installs the ``capability_scope`` guard FIRST (because a
      ``db_factory`` is given) and the per-child model via ``build_chat_model`` (sonnet
      for the Perceiver, thinking preserved). There is NO ``trust_gate`` / ``write_lock``
      middleware — the delegate is read-only.

    Args:
        agent_config: The Jarvis sub-agent driving the delegate (its ``capability_scope``,
            model tier + thinking, and default prompt). Use the in-memory Perceiver from
            ``create_sub_agents()`` (thinking preserved), NOT ``load_as_sub_agents``.
        tools: Jarvis tool defs (dicts with ``name`` / ``description`` / ``input_schema``)
            the delegate may attempt; capability-resolved at runtime by the scope guard.
        workspace_id: Tenant scope; captured in the dispatcher/guard, never LLM-supplied.
        user_id: Authenticated user ID for this turn; captured in the dispatcher closure.
        db_factory: Async-context-manager factory yielding an ``AsyncSession`` for the
            scope guard's registry lookups. Passing it installs the guard.
        execute_tool: Async callable ``(name, args, user_id, workspace_id) -> dict`` used
            by the central dispatcher.
        system_prompt: Override for the delegate's role prompt; defaults to
            ``agent_config.prompt``. This is its OWN prompt — never the lead's
            Presenter-voice inline-format augmentation.
        name: Override for the delegate name; defaults to ``agent_config.name``.
        description: Override for the ``task``-tool description; defaults to a
            "Read-only research delegate" line.

    Returns:
        A ``CompiledSubAgent`` dict: ``{"name", "description", "runnable"}`` where
        ``runnable`` is a compiled deepagents graph ready for ``.ainvoke()``.
    """
    shells = build_tool_shells(tools)
    dispatcher = make_jarvis_tool_dispatcher(
        execute_tool=execute_tool,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    compiled = await build_deep_agent(
        agent_config,
        shells,
        workspace_id=workspace_id,
        db_factory=db_factory,
        extra_middleware=(dispatcher,),
        system_prompt=system_prompt or agent_config.prompt,
    )
    return {
        "name": name or agent_config.name,
        "description": description or f"Read-only research delegate ({agent_config.name}).",
        "runnable": compiled,
    }


def disable_general_purpose_subagent(model_name: str) -> None:
    """Disable the ambient auto-added general-purpose ``task`` subagent for a deep lead.

    ``create_deep_agent`` auto-inserts a stock ``general-purpose`` subagent (backing the
    built-in ``task`` tool) unless a harness profile disables it. On a Jarvis delegate
    host we want the lead's only ``task`` targets to be the read-only Jarvis delegates
    explicitly registered via ``subagents=[...]`` — never an unscoped general-purpose
    child. This registers a deepagents ``HarnessProfile`` whose
    ``general_purpose_subagent.enabled`` is ``False`` under ``anthropic:<model_name>``,
    so a lead whose model resolves to that key is built without the GP child.

    Model-scoped by design: the key is ``f"anthropic:{model_name}"`` (the direct
    Anthropic model id, e.g. ``claude-sonnet-4-6``).
    Verified in the Phase-0 spike as preferred over the provider-wide ``"anthropic"``
    key — disabling GP for a sonnet lead leaves GP intact for opus/haiku agents built
    in the same process.

    Idempotent + quiet: if the key is already GP-disabled this returns early without
    re-registering, so the Phase-4 seam (which calls this on every deep delegate-host
    lead build) does not trigger deepagents' additive-merge INFO log on every turn.

    PROCESS-GLOBAL SCOPE — AUDITED + ACCEPTED (Step-10A A5). ``register_harness_profile``
    mutates a process-global registry, so the disable persists for the process lifetime
    and affects EVERY deep lead whose model resolves to ``anthropic:{model_name}``. This
    is ACCEPTABLE and intentional: (a) it is key-scoped to one model id, so opus/haiku
    leads are unaffected; (b) the ONLY effect is dropping the auto-added general-purpose
    ``task`` child — a Jarvis delegate host wants exactly that (its ``task`` targets are
    the explicitly registered read-only Jarvis delegates, never an unscoped GP child);
    (c) it is dormant (called only under ``deep_delegates_enabled``) and idempotent. For
    a bounded, reversible scope (tests, or any caller needing to undo), use the
    ``general_purpose_disabled`` context-manager (restore-not-pop) — a naive pop would
    delete a pre-existing profile.

    Args:
        model_name: The direct Anthropic model id of the lead (e.g. ``"claude-sonnet-4-6"``).
            The harness-profile key is ``f"anthropic:{model_name}"``.
    """
    # Private-registry read for the idempotency guard only; the write goes through the
    # public register_harness_profile (which owns lazy built-in bootstrap + merge).
    from deepagents.profiles.harness.harness_profiles import _HARNESS_PROFILES

    key = f"anthropic:{model_name}"
    existing = _HARNESS_PROFILES.get(key)
    if existing is not None:
        gp = existing.general_purpose_subagent
        if gp is not None and gp.enabled is False:
            return  # already disabled — stay quiet (no re-merge, no INFO log spam)

    register_harness_profile(
        key,
        HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
    )


@contextmanager
def general_purpose_disabled(model_name: str):
    """Bounded-scope form of ``disable_general_purpose_subagent`` that RESTORES the prior
    harness profile on exit (restore-not-pop): a pre-existing profile for this key — including
    a deepagents built-in bootstrap — survives the block, and a key we newly added is removed.

    Use this in tests and any bounded-scope caller. The LIVE delegate seam
    (``_build_delegate_subagents``) uses the imperative ``disable_general_purpose_subagent``
    instead — there the disable intentionally PERSISTS for the process (audited, see that
    function's docstring). A plain ``enable_general_purpose_subagent`` is deliberately NOT
    provided: "restore the prior" requires the captured prior value, which only this
    context-manager owns.
    """
    from deepagents.profiles.harness.harness_profiles import _HARNESS_PROFILES

    key = f"anthropic:{model_name}"
    had_prior = key in _HARNESS_PROFILES
    prior = _HARNESS_PROFILES.get(key)
    disable_general_purpose_subagent(model_name)
    try:
        yield
    finally:
        if had_prior:
            _HARNESS_PROFILES[key] = prior  # restore the EXACT prior (built-in survives)
        else:
            _HARNESS_PROFILES.pop(key, None)  # nothing before -> remove only what we added
