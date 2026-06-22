"""Unit tests for the insight-title cleaning helpers in surface_pusher.

These guard the D1 fix: raw pipeline prose ("Polled gmail: 1 new event(s).
- [gmail] email_received: ... (event_id=evt_...)") must never reach a
user-facing surface title. The helpers strip the "[source] type:" prefix,
the "(event_id=...)" suffix, and build a concise human headline.
"""

from src.orchestrator.surface_pusher import (
    _clean_event_subject,
    _clean_insight_title,
)


def test_clean_event_subject_strips_prefix_and_event_id():
    line = "- [gmail] email_received: INR 1087 spent on card XX3971 (event_id=evt_01KVK5E87)"
    assert _clean_event_subject(line) == "INR 1087 spent on card XX3971"


def test_clean_event_subject_without_event_id():
    line = "- [slack] message_received: Standup at 10am"
    assert _clean_event_subject(line) == "Standup at 10am"


def test_clean_event_subject_no_bullet():
    line = "[calendar] event_created: Lunch with Sarah (event_id=evt_99)"
    assert _clean_event_subject(line) == "Lunch with Sarah"


def test_clean_event_subject_empty_subject_returns_empty():
    line = "- [gmail] email_received: "
    assert _clean_event_subject(line) == ""


def test_clean_insight_title_single_event_strips_jargon():
    raw = (
        "Polled gmail: 1 new event(s).\n"
        "- [gmail] email_received: INR 1087 spent on credit card no. XX3971 "
        "(event_id=evt_01KVK5E87)"
    )
    assert _clean_insight_title(raw) == "INR 1087 spent on credit card no. XX3971"


def test_clean_insight_title_multiple_events_counts_and_shows_first():
    raw = (
        "Polled gmail: 2 new event(s).\n"
        "- [gmail] email_received: Invoice from Acme (event_id=evt_01)\n"
        "- [gmail] email_received: Lunch tomorrow? (event_id=evt_02)"
    )
    assert _clean_insight_title(raw) == "2 new updates: Invoice from Acme"


def test_clean_insight_title_ignores_thread_context_section():
    raw = (
        "Polled gmail: 1 new event(s).\n"
        "- [gmail] email_received: Series A update (event_id=evt_01)\n"
        "\n"
        "--- Thread Context (full conversation) ---\n"
        "Thread t1 (3 messages):\n"
        "  [sarah]: looping back on the term sheet"
    )
    # Only the one real event subject should drive the title.
    assert _clean_insight_title(raw) == "Series A update"


def test_clean_insight_title_truncates_to_max_len():
    long_subject = "A" * 200
    raw = (
        f"Polled gmail: 1 new event(s).\n- [gmail] email_received: {long_subject} (event_id=evt_01)"
    )
    title = _clean_insight_title(raw)
    assert len(title) <= 120
    assert title.endswith("…")


def test_clean_insight_title_custom_max_len():
    raw = "Polled gmail: 1 new event(s).\n- [gmail] email_received: Hello world (event_id=evt_01)"
    assert _clean_insight_title(raw, max_len=5) == "Hell…"


def test_clean_insight_title_empty_summary_falls_back():
    assert _clean_insight_title("") == "New activity"


def test_clean_insight_title_no_event_lines_falls_back():
    raw = "Polled gmail: 0 new event(s)."
    assert _clean_insight_title(raw) == "New activity"


def test_clean_insight_title_never_contains_event_id_or_prefix():
    raw = (
        "Polled gmail: 2 new event(s).\n"
        "- [gmail] email_received: Payment received (event_id=evt_01KVK5E87ABCD)\n"
        "- [gmail] email_received: Refund issued (event_id=evt_01KVK5E99WXYZ)"
    )
    title = _clean_insight_title(raw)
    assert "event_id=" not in title
    assert "evt_" not in title
    assert "[gmail]" not in title
    assert "email_received" not in title
    assert "Polled" not in title
