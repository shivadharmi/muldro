"""GitHub is the one connector that keys on an occurrence, not a thing.

gmail keys on threadId, slack on thread_ts, calendar on the event id, notion
on the page id. github keyed on the NOTIFICATION id, so one PR collecting
three review comments minted three identities. subject.url already names the
PR itself.
"""

from datetime import datetime, timezone

from src.connectors.github_connector import GitHubConnector


def _notification(**overrides):
    notif = {
        "id": "notif_999",
        "updated_at": "2026-08-21T14:14:00Z",
        "reason": "review_requested",
        "repository": {"full_name": "shivadharmi/muldrov1"},
        "subject": {
            "type": "PullRequest",
            "title": "Single-lead cutover",
            "url": "https://api.github.com/repos/shivadharmi/muldrov1/pulls/19",
        },
    }
    notif.update(overrides)
    return notif


def test_entity_id_is_the_pull_request_not_the_notification():
    raw = GitHubConnector._normalize_notification(_notification())
    assert raw.entity_id == "shivadharmi/muldrov1#19"
    assert raw.entity_id != "notif_999"


def test_two_notifications_on_one_pr_share_an_entity_id():
    first = GitHubConnector._normalize_notification(_notification(id="notif_1"))
    second = GitHubConnector._normalize_notification(_notification(id="notif_2"))
    assert first.entity_id == second.entity_id


def test_issue_url_is_parsed_too():
    raw = GitHubConnector._normalize_notification(
        _notification(
            subject={
                "type": "Issue",
                "title": "Card opens to nothing",
                "url": "https://api.github.com/repos/shivadharmi/muldrov1/issues/42",
            }
        )
    )
    assert raw.entity_id == "shivadharmi/muldrov1#42"


def test_falls_back_to_the_notification_id_when_the_url_is_unparseable():
    raw = GitHubConnector._normalize_notification(
        _notification(subject={"type": "Release", "title": "v2", "url": None})
    )
    assert raw.entity_id == "notif_999"


def test_actor_names_the_repo_so_the_frame_can_compose_a_headline():
    """The human commenter is not in the notifications payload, so the repo is
    the only counterparty github offers - and the frame needs one, because the
    title is now the bare subject.

    `type` is descriptive only and NOTHING READS IT: this asserted the field
    under a docstring claiming it stopped the headline builder presenting a
    repo as a person, which it never did. The headline is asserted below,
    since that is the behaviour the claim was about.
    """
    raw = GitHubConnector._normalize_notification(_notification())
    assert raw.actor["name"] == "shivadharmi/muldrov1"
    assert raw.actor["type"] == "repository"


def test_the_headline_names_the_repo_and_the_subject():
    """What the founder actually sees. A github notification reads
    "acme/web - Add retry to poller", formatted exactly like a person's
    "Sarah Chen - Series A term sheet" - by design: dropping a non-person
    actor would delete the repo from every github headline, and the repo is
    the only thing distinguishing one notification from another.
    """
    from src.view.frame import frame_for_event

    raw = GitHubConnector._normalize_notification(_notification())

    assert frame_for_event(raw).headline == "shivadharmi/muldrov1 - Single-lead cutover"


def test_notification_id_is_retained_in_the_payload():
    raw = GitHubConnector._normalize_notification(_notification())
    assert raw.raw_payload["notification_id"] == "notif_999"


def test_title_is_the_bare_subject_with_no_repo_prefix():
    """The repo travels in actor now; the frame composes the headline.

    A connector that pre-formats "[repo] subject" is writing user-facing
    text, which is the frame's job.
    """
    raw = GitHubConnector._normalize_notification(_notification())
    assert raw.title == "Single-lead cutover"


def test_entity_type_stays_the_raw_lowercased_subject_type():
    """frame.key is (source, entity_type, entity_id); the archetype mapping
    reads entity_type. Do not prettify it to 'pull_request'."""
    raw = GitHubConnector._normalize_notification(_notification())
    assert raw.entity_type == "pullrequest"


# --- the parser itself: a wrong id is worse than a fallback -----------------
#
# A wrong id silently merges two different things into one card, which is the
# exact defect this change exists to remove. Every malformed shape below must
# reach the fallback rather than invent an id or raise.


def _parse(url, fallback):
    # Resolved at call time so a missing helper fails these tests rather than
    # erroring out collection of the whole module.
    return GitHubConnector._entity_id_from_subject_url(url, fallback)


def test_parses_a_well_formed_pull_request_url():
    assert (
        _parse("https://api.github.com/repos/shivadharmi/muldrov1/pulls/19", "fb")
        == "shivadharmi/muldrov1#19"
    )


def test_a_trailing_slash_still_parses():
    assert (
        _parse("https://api.github.com/repos/shivadharmi/muldrov1/pulls/19/", "fb")
        == "shivadharmi/muldrov1#19"
    )


def test_a_query_string_still_parses():
    assert (
        _parse("https://api.github.com/repos/shivadharmi/muldrov1/pulls/19?foo=1", "fb")
        == "shivadharmi/muldrov1#19"
    )


def test_an_owner_or_repo_literally_named_repos_is_not_confused():
    """The API prefix is always the FIRST /repos/ segment."""
    assert _parse("https://api.github.com/repos/repos/repos/pulls/7", "fb") == "repos/repos#7"


def test_a_deeper_url_falls_back_rather_than_returning_the_comment_id():
    """The number must sit in the number POSITION, not merely at the end.

    .../issues/comments/12345 is a comment, not issue 12345 - keying on the
    trailing digits there would merge every comment thread onto one card.
    """
    url = "https://api.github.com/repos/shivadharmi/muldrov1/issues/comments/12345"
    assert _parse(url, "fb") == "fb"


def test_a_commit_sha_url_falls_back():
    url = "https://api.github.com/repos/shivadharmi/muldrov1/commits/abc123def"
    assert _parse(url, "fb") == "fb"


def test_a_url_with_no_repos_segment_falls_back():
    assert _parse("https://github.com/shivadharmi/muldrov1/discussions/5", "fb") == "fb"


def test_a_truncated_url_falls_back():
    assert _parse("https://api.github.com/repos/shivadharmi", "fb") == "fb"
    assert _parse("https://api.github.com/repos", "fb") == "fb"
    assert _parse("", "fb") == "fb"


def test_a_non_string_url_falls_back_without_raising():
    assert _parse(None, "fb") == "fb"
    assert _parse(12345, "fb") == "fb"
    assert _parse({"url": "x"}, "fb") == "fb"


# --- occurred_at: the notification's updated_at IS the event's timestamp ----
#
# Without it every GitHub RawEvent carried occurred_at=None, and the two
# consumers disagree about what that means: the frame builder falls back to
# now() while the feed grouper sorts a missing timestamp at datetime.min. One
# event would render "just now" on a card the feed had ordered at year-1.


def test_occurred_at_comes_from_updated_at():
    raw = GitHubConnector._normalize_notification(_notification())
    assert raw.occurred_at == datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc)


def test_occurred_at_is_timezone_aware():
    """Notion is the one connector that forgot this; do not become the second.

    A naive value here raises on any comparison against an aware one.
    """
    raw = GitHubConnector._normalize_notification(_notification())
    assert raw.occurred_at is not None
    assert raw.occurred_at.tzinfo is not None


def test_a_missing_updated_at_is_none_and_does_not_raise():
    notif = _notification()
    del notif["updated_at"]
    raw = GitHubConnector._normalize_notification(notif)
    assert raw.occurred_at is None


def test_an_unparseable_updated_at_is_none_and_does_not_raise():
    """A crash here would take down the whole poll for the source."""
    raw = GitHubConnector._normalize_notification(_notification(updated_at="not a date"))
    assert raw.occurred_at is None


def test_an_empty_or_non_string_updated_at_is_none():
    for bad in ("", "   ", None, 1755787200, {"at": "now"}):
        raw = GitHubConnector._normalize_notification(_notification(updated_at=bad))
        assert raw.occurred_at is None, bad


def test_a_naive_updated_at_is_coerced_to_utc_rather_than_left_naive():
    raw = GitHubConnector._normalize_notification(_notification(updated_at="2026-08-21T14:14:00"))
    assert raw.occurred_at == datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc)


def test_an_offset_updated_at_is_normalized_to_utc():
    raw = GitHubConnector._normalize_notification(
        _notification(updated_at="2026-08-21T19:44:00+05:30")
    )
    assert raw.occurred_at == datetime(2026, 8, 21, 14, 14, tzinfo=timezone.utc)
    assert raw.occurred_at.tzinfo is timezone.utc
