import asyncio
from unittest.mock import AsyncMock, patch

from src.services.event_processor import RawEvent
from src.services.triage import (
    CATEGORY_TIER,
    TriageService,
    classify_by_rules,
    derive_tier,
    is_actionable,
)


def _run(coro):
    return asyncio.run(coro)


def _raw(headers=None, sender="a@b.com", title="hi"):
    return RawEvent(
        source="gmail",
        source_account_id="acc",
        event_type="email_received",
        entity_type="email",
        entity_id="e1",
        title=title,
        summary="s",
        actor={"email": sender},
        raw_payload={"headers": headers or {}},
    )


def test_unsubscribe_header_is_marketing():
    assert classify_by_rules(_raw(headers={"List-Unsubscribe": "<mailto:x>"})) == "marketing"


def test_precedence_bulk_is_marketing():
    assert classify_by_rules(_raw(headers={"Precedence": "bulk"})) == "marketing"


def test_plain_personal_mail_has_no_rule():
    assert classify_by_rules(_raw()) is None


def test_derive_tier_maps_category():
    assert derive_tier("marketing") == "skip"
    assert derive_tier("financial") == "light"
    assert derive_tier("security_alert") == "full"
    assert derive_tier("otp") == "skip"  # transient one-time codes are noise, not alerts
    assert derive_tier("totally_unknown") == "full"  # fail-safe: unknown → full (recall)
    # derive_tier must agree with the CATEGORY_TIER table for every known category.
    for category, tier in CATEGORY_TIER.items():
        assert derive_tier(category) == tier


def test_is_actionable_requires_category_and_urgency():
    assert is_actionable("security_alert", urgency=0.9) is True
    assert is_actionable("security_alert", urgency=0.1) is False
    assert is_actionable("marketing", urgency=0.9) is False


def test_triage_batch_rules_skip_llm_when_all_rule_classified():
    svc = TriageService()
    events = [
        _raw(headers={"List-Unsubscribe": "<x>"}, title="Sale"),
        _raw(headers={"Precedence": "bulk"}, title="Promo"),
    ]
    with patch("src.services.triage.complete_text", new=AsyncMock()) as mock_llm:
        results = _run(svc.triage_batch(events, user_id="u"))
    mock_llm.assert_not_called()  # zero LLM calls for an all-marketing batch
    assert [r.tier for r in results] == ["skip", "skip"]
    assert all(r.origin == "rules" for r in results)


def test_triage_batch_llm_classifies_remainder():
    svc = TriageService()
    events = [
        _raw(headers={"List-Unsubscribe": "<x>"}, title="Sale"),  # rule → marketing
        _raw(sender="cofounder@startup.com", title="Board deck review"),  # → llm
    ]
    llm_json = (
        '[{"category":"work_thread","importance_score":0.8,'
        '"urgency_score":0.6,"confidence_score":0.9}]'
    )
    with patch(
        "src.services.triage.complete_text", new=AsyncMock(return_value=llm_json)
    ) as mock_llm:
        results = _run(svc.triage_batch(events, user_id="u"))
    mock_llm.assert_called_once()  # only the 1 ambiguous event went to the LLM
    assert results[0].tier == "skip" and results[0].origin == "rules"
    assert results[1].tier == "full" and results[1].category == "work_thread"
    assert results[1].actionable is True  # work_thread + urgency 0.6 >= 0.4
