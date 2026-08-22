"""The words the founder reads when staged work is dropped.

A staged (``prepared_action``) approval is a write that a gate recorded instead of
executing, because nobody was reachable to review it. When it ages out unanswered the
action is simply gone — and the realistic failure mode is a founder who assumes staged
work eventually ran. So the body says, first and plainly, that nothing was performed.

Composition lives here rather than inline in the heartbeat because it is pure text over
a list: it has no session, no clock and no notifier, and can therefore be asserted on
directly. Notification of the batch — one message per cycle, never one per item —
belongs to the caller.
"""

from __future__ import annotations

# Beyond this the list is elided. A backlog of staged work must not produce an
# unbounded notification body.
MAX_LISTED = 5

_UNTITLED = "Untitled action"


def expired_prepared_notice(approvals) -> tuple[str, str] | None:
    """Return ``(title, body)`` for a batch of expired staged actions, or None for none.

    Returning None for an empty batch is what keeps the caller from sending a
    notification about nothing.
    """
    count = len(approvals)
    if count == 0:
        return None

    single = count == 1
    noun = "action" if single else "actions"
    subject = "This action" if single else "These actions"
    was = "was" if single else "were"
    it = "It" if single else "They"
    has = "has" if single else "have"
    its = "its" if single else "their"
    them = "it" if single else "them"

    title = f"{count} staged {noun} expired unreviewed"

    titles = [(getattr(a, "title", None) or _UNTITLED) for a in approvals]
    lines = [f"- {t}" for t in titles[:MAX_LISTED]]
    remaining = count - len(lines)
    if remaining > 0:
        lines.append(f"- ...and {remaining} more")

    body = "\n".join(
        [
            f"{subject} {was} NOT performed. {it} waited unanswered past "
            f"{its} review deadline and {has} now been dropped.",
            "",
            *lines,
            "",
            f"Nothing was sent, created or changed. {subject} cannot be "
            f"recovered — ask for {them} again if you still want {them} done.",
        ]
    )
    return title, body
