"""Tiered event triage — classify events cheaply so extraction cost is
proportional to value. Deterministic header rules run first; the ambiguous
remainder is classified by one batched Haiku call (TriageService, Task 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tier = Literal["skip", "light", "full"]

# category → tier. Unknown categories fall through to "full" (recall-preserving).
CATEGORY_TIER: dict[str, Tier] = {
    "marketing": "skip",
    "newsletter": "skip",
    "social_notification": "skip",
    "delivery_ping": "skip",
    "financial": "light",
    "transactional": "light",
    "personal": "full",
    "work_thread": "full",
    "security_alert": "full",
    "calendar_invite": "full",
    "direct_request": "full",
}

ACTIONABLE_CATEGORIES = {
    "security_alert",
    "calendar_invite",
    "direct_request",
    "work_thread",
}
ACTIONABLE_URGENCY_THRESHOLD = 0.4
DEFAULT_CATEGORY = "personal"  # fail-safe when the LLM omits/garbles a category


def derive_tier(category: str) -> Tier:
    """Map a category to its extraction tier. Unknown → full (never silently drop)."""
    return CATEGORY_TIER.get(category, "full")


def is_actionable(category: str, urgency: float) -> bool:
    """Whether an event should be allowed to wake the Opus Planner."""
    return category in ACTIONABLE_CATEGORIES and urgency >= ACTIONABLE_URGENCY_THRESHOLD


def classify_by_rules(raw) -> str | None:
    """High-precision deterministic classification. Returns a category or None.

    Only fires for high-confidence *skip* signals so it never wrongly suppresses
    a real message: bulk-mail headers that legitimate marketing/newsletters are
    legally required to carry. Everything else defers to the LLM (Task 3).
    """
    payload = getattr(raw, "raw_payload", None) or {}
    headers = {str(k).lower(): str(v) for k, v in (payload.get("headers") or {}).items()}
    if "list-unsubscribe" in headers or "list-id" in headers:
        return "marketing"
    if headers.get("precedence", "").lower() in {"bulk", "list", "junk"}:
        return "marketing"
    return None


@dataclass
class TriageResult:
    category: str
    tier: Tier
    actionable: bool
    importance_score: float
    urgency_score: float
    confidence_score: float
    origin: Literal["rules", "llm", "default"]

    def to_signals(self) -> dict:
        """Serialize the triage fields for persistence in importance_signals."""
        return {
            "category": self.category,
            "tier": self.tier,
            "actionable": self.actionable,
            "triage_origin": self.origin,
        }
