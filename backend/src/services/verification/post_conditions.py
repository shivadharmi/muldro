"""Per-capability post-condition registry + the startup coverage gate (spec §4.5).

A PostCondition declares HOW to read back an irreversible write's effect: a
`read_capability` (invoked through the tool-execution seam) and an `assertion` over
its result + the original write's args/output. Where no deterministic read exists,
the capability is listed in UNVERIFIABLE_CAPABILITIES instead (explicit → the effect
is honestly marked completed_unverified, never silently).

Coverage invariant (mirrors validate_registry): every IRREVERSIBLE write capability
MUST be in POST_CONDITIONS or UNVERIFIABLE_CAPABILITIES — enforced as a startup error
so a new write capability can't silently skip verification on the irreversible path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src.services.verification.predicate import is_irreversible_capability


@dataclass(frozen=True, slots=True)
class PostCondition:
    """A read-back check for one write capability.

    read_capability: the capability to invoke to observe the effect (e.g.
      "calendar.get" after "calendar.create"). Run through the injected
      execute_tool_fn by ReadBackVerifier.
    read_args: build the read tool's input from the write's (input_data, output).
    assertion: given the read result, return True iff the expected effect is present.
    """

    read_capability: str
    read_args: Callable[[dict, dict], dict]
    assertion: Callable[[object, dict, dict], bool]
    description: str = ""


def _event_created(read_result, write_input: dict, write_output: dict) -> bool:
    """A created calendar event is confirmed when the read-back returns an event
    whose id matches the one the write reported."""
    created_id = (write_output or {}).get("event_id") or (write_output or {}).get("id")
    if not created_id:
        return False
    items = read_result if isinstance(read_result, list) else [read_result]
    return any(isinstance(it, dict) and it.get("id") == created_id for it in items)


# Capabilities WITH a deterministic read-back. Kept minimal for the MVP — one worked
# example proving the mechanism end-to-end (mocked in tests). Real per-connector
# read-backs are added over time; until then a capability lives in
# UNVERIFIABLE_CAPABILITIES (honest completed_unverified), never silently skipped.
POST_CONDITIONS: dict[str, PostCondition] = {
    "calendar.create": PostCondition(
        read_capability="calendar.get",
        read_args=lambda write_input, write_output: {
            "event_id": (write_output or {}).get("event_id") or (write_output or {}).get("id"),
            "calendar_id": (write_input or {}).get("calendar_id"),
        },
        assertion=_event_created,
        description=(
            "Read the created event back by id to confirm it landed. NOTE (MVP): this is "
            "the worked example proving the read-back mechanism — exercised via mock in "
            "tests (D8). On this branch calendar.get is backed by query_freebusy "
            "(free/busy ranges, not events-by-id), so a LIVE read-back cannot id-match; "
            "keep this mock-only until a real get-event tool exists, or a live run may "
            "false-CONTRADICT (low-harm: escalate-first only surfaces a 'couldn't "
            "confirm' alarm to a present user)."
        ),
    ),
}


# IRREVERSIBLE write capabilities with NO deterministic read-back today (eventually
# consistent APIs, no stable id returned, or no read capability). These resolve to
# completed_unverified — an explicit, audited decision, NOT a silent gap. Adding a
# real read-back = move the capability from here into POST_CONDITIONS.
UNVERIFIABLE_CAPABILITIES: frozenset[str] = frozenset(
    {
        "email.send",
        "email.reply",
        "email.delete",
        "calendar.update",
        "calendar.delete",
        "repo.create_pr",
        "repo.merge_pr",
        "repo.update_pr",
        "repo.review_pr",
        "issue.create",
        "issue.update",
        "issue.comment",
        "issue.delete",
        "issue.transition",
        "issue.sub_issue",
        "doc.create",
        "doc.update",
        "doc.delete",
        "doc.comment",
        "doc.append",
        "doc.move",
        "doc.update_block",
        "doc.delete_block",
        "doc.create_datasource",
        "doc.update_datasource",
        "doc.drive_create",
        "doc.drive_delete",
        "workflow.create_issue",
        "workflow.update_issue",
        "workflow.transition",
        "workflow.comment",
        "workflow.delete",
        "workflow.create_issues",
        "workflow.bulk_update",
        "workflow.update_comment",
        "workflow.delete_comment",
        "workflow.resolve_comment",
        "workflow.unresolve_comment",
        "workflow.create_project",
        "workflow.create_milestone",
        "workflow.update_milestone",
        "workflow.delete_milestone",
        "workflow.create_customer_need",
        "messaging.send",
        "messaging.reply",
        "messaging.react",
        "messaging.update",
        "messaging.send_template",
        "messaging.post",
        "messaging.share",
        "browser.open",
        "browser.click",
        "browser.type",
        "browser.submit",
        "browser.execute",
        "browser.install",
    }
)


def validate_post_condition_coverage(write_capabilities: set[str]) -> list[str]:
    """Return error strings for IRREVERSIBLE write capabilities that are NOT
    registered (neither a PostCondition nor an explicit UNVERIFIABLE marker).

    Mirrors validate_registry(): returns list[str] (empty = valid), never raises.
    The caller (startup gate) decides fatality.
    """
    errors: list[str] = []
    registered = set(POST_CONDITIONS) | UNVERIFIABLE_CAPABILITIES
    for cap in sorted(write_capabilities):
        if not is_irreversible_capability(cap):
            continue
        if cap not in registered:
            errors.append(
                f"IRREVERSIBLE capability '{cap}' has no registered post-condition "
                "(add a PostCondition to POST_CONDITIONS or mark it UNVERIFIABLE)"
            )
    return errors
