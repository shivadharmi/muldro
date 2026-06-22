"""Model-aware thinking/effort helpers for the Deep Agents runtime.

These are ported VERBATIM (intent-preserving) from
``src.orchestrator.agent_loop`` so ``deep_runtime`` stays self-contained and
import-light — it must not import private helpers from ``agent_loop`` (which
pulls in the whole legacy runtime). When the legacy runtime is deleted in
Phase 5, this module is the single source of truth for the adaptive-vs-legacy
thinking split.

Adaptive-only models (Opus 4.7/4.8, Fable 5, Mythos 5) reject ``temperature``
and the legacy ``thinking:{type:"enabled", budget_tokens}`` shape; they require
``thinking:{type:"adaptive"}`` + an ``effort`` tier. Legacy models keep the
enabled-thinking + temperature surface they still accept.
"""

from __future__ import annotations

# Models whose API rejects ``temperature`` and the legacy enabled-thinking shape
# — they require adaptive thinking + effort. Matched as substrings so Bedrock
# inference-profile IDs (e.g. us.anthropic.claude-opus-4-8) are covered too.
# MAINTENANCE: every new adaptive-only model (e.g. a future Opus 4.9) MUST be
# added here, or it falls through to the legacy path and 400s on every call.
ADAPTIVE_THINKING_MARKERS: tuple[str, ...] = (
    "opus-4-8",
    "opus-4-7",
    "fable-5",
    "mythos-5",
    "mythos-preview",
)


def requires_adaptive_thinking(model: str) -> bool:
    """True when *model* rejects temperature + enabled-thinking (adaptive only)."""
    m = (model or "").lower()
    return any(marker in m for marker in ADAPTIVE_THINKING_MARKERS)


def effort_for_budget(budget_tokens: int | None) -> str:
    """Map a legacy per-agent thinking budget to an effort tier.

    Preserves the relative intent (Planner=8192 thinks hardest) without sending
    the now-rejected token budget. Default high — the recommended floor for
    intelligence-sensitive work on Opus 4.7/4.8.
    """
    if not budget_tokens or budget_tokens >= 8192:
        return "high"
    if budget_tokens >= 4096:
        return "medium"
    return "low"
