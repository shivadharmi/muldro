"""Tiered event triage — classify events cheaply so extraction cost is
proportional to value. Deterministic header rules run first; the ambiguous
remainder is classified by one batched Haiku call (TriageService, Task 3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.llm.utility import complete_text
from src.llm_utils import parse_llm_json

logger = logging.getLogger(__name__)

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


TRIAGE_SYSTEM_PROMPT = """\
You are Jarvis's event triage engine. For each event, assign a category and \
importance/urgency/confidence scores (floats 0.0-1.0).

Categories (choose exactly one per event):
- marketing: promotions, offers, sales, ads
- newsletter: digests, subscriptions, bulk editorial
- social_notification: likes, follows, platform notifications
- delivery_ping: order/shipping/delivery status with no durable fact
- financial: card charges, receipts, invoices, subscription renewals
- transactional: account/service notices carrying a durable fact
- personal: mail from a real individual to the user
- work_thread: work/project discussion, colleagues, collaborators
- security_alert: logins, passkeys, password/2FA, suspicious activity
- calendar_invite: meeting invites/updates needing awareness or response
- direct_request: an explicit ask/question requiring the user to act

Respond with a JSON array of objects in the SAME ORDER as the events, each:
{"category": "<one of the above>", "importance_score": float, "urgency_score": float, \
"confidence_score": float}
"""


class TriageService:
    """Classify events into extraction tiers. Rules first, one batched Haiku
    call for the remainder. Failures fall back to full-tier (recall-preserving)."""

    def _default(self, origin: Literal["rules", "llm", "default"] = "default") -> TriageResult:
        return TriageResult(
            category=DEFAULT_CATEGORY,
            tier=derive_tier(DEFAULT_CATEGORY),
            actionable=False,
            importance_score=0.5,
            urgency_score=0.3,
            confidence_score=0.3,
            origin=origin,
        )

    def _from_llm_obj(self, obj: dict) -> TriageResult:
        category = str(obj.get("category") or DEFAULT_CATEGORY)
        urgency = float(obj.get("urgency_score", 0.3) or 0.3)
        return TriageResult(
            category=category,
            tier=derive_tier(category),
            actionable=is_actionable(category, urgency),
            importance_score=float(obj.get("importance_score", 0.5) or 0.5),
            urgency_score=urgency,
            confidence_score=float(obj.get("confidence_score", 0.3) or 0.3),
            origin="llm",
        )

    async def triage_batch(self, events: list, user_id: str) -> list[TriageResult]:
        """Classify a batch of raw events. Deterministic rules run first; the
        ambiguous remainder is classified by a single batched Haiku call so
        cost stays proportional to the ambiguous fraction of the batch."""
        results: list[TriageResult | None] = [None] * len(events)
        remainder: list[tuple[int, object]] = []

        # 1. Deterministic pass — high-precision skip signals, no LLM cost.
        for i, raw in enumerate(events):
            cat = classify_by_rules(raw)
            if cat is not None:
                results[i] = TriageResult(
                    category=cat,
                    tier=derive_tier(cat),
                    actionable=False,
                    importance_score=0.05,
                    urgency_score=0.05,
                    confidence_score=0.9,
                    origin="rules",
                )
            else:
                remainder.append((i, raw))

        # 2. One batched Haiku call for whatever the rules couldn't classify.
        if remainder:
            llm_results = await self._classify_llm([r for _, r in remainder])
            for (idx, _), res in zip(remainder, llm_results, strict=True):
                results[idx] = res

        return [r if r is not None else self._default() for r in results]

    async def _classify_llm(self, events: list) -> list[TriageResult]:
        """Classify *events* in a single Haiku call. Falls back to the
        recall-preserving default (full tier) on any parse/count failure."""
        parts = []
        for i, raw in enumerate(events, 1):
            sender = (getattr(raw, "actor", None) or {}).get("email", "unknown")
            parts.append(
                f"Event {i}:\n  From: {sender}\n  Title: {getattr(raw, 'title', '') or ''}"
                f"\n  Summary: {getattr(raw, 'summary', '') or ''}"
            )
        user_msg = "Classify these events:\n\n" + "\n\n".join(parts)
        try:
            text = await complete_text(
                system=TRIAGE_SYSTEM_PROMPT,
                user=user_msg,
                tier="haiku",
                max_tokens=128 * len(events),
            )
            parsed = parse_llm_json(text, default=[])
            if isinstance(parsed, dict):
                # Tolerate a model that wraps the array under a key despite
                # instructions asking for a bare array.
                parsed = parsed.get("events") or parsed.get("results") or []
            if isinstance(parsed, list) and len(parsed) == len(events):
                return [self._from_llm_obj(o) for o in parsed]
            logger.warning(
                "Triage LLM returned %s results for %d events",
                len(parsed) if isinstance(parsed, list) else "non-list",
                len(events),
            )
        except Exception:
            logger.warning("Triage LLM failed; defaulting remainder to full", exc_info=True)
        return [self._default(origin="default") for _ in events]
