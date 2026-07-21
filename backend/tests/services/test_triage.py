from src.services.event_processor import RawEvent
from src.services.triage import (
    CATEGORY_TIER,
    classify_by_rules,
    derive_tier,
    is_actionable,
)


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
    assert derive_tier("totally_unknown") == "full"  # fail-safe: unknown → full (recall)
    # derive_tier must agree with the CATEGORY_TIER table for every known category.
    for category, tier in CATEGORY_TIER.items():
        assert derive_tier(category) == tier


def test_is_actionable_requires_category_and_urgency():
    assert is_actionable("security_alert", urgency=0.9) is True
    assert is_actionable("security_alert", urgency=0.1) is False
    assert is_actionable("marketing", urgency=0.9) is False
