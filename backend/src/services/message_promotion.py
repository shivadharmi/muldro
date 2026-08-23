"""Agent-message workspace-feed promotion gate.

The Presenter always produces a chat response, but only some of those
responses are rich enough to deserve a spot in the workspace feed.

Design decision: the gate is **structural**, not semantic. The agent does not
self-evaluate "usefulness" — the gate looks at what was actually built. That
avoids both false positives (the agent overestimates its own importance) and
false negatives (it underestimates multi-part analysis). That principle is
correct and is kept.

The component-tree walker that used to apply it is gone. Walking a
model-authored component tree to decide importance was itself a semantic
judgement wearing a structural name — the model chose the components, so
"it built a Table" is the model rating itself one step removed. "Did this
turn produce something durable" is answered by what the turn DID: a run row
was created, a write was staged for review, a finding was recorded.

This module currently has NO CALLER. That is deliberate: the principle
outlives the walker, and rewiring it to the turn's real output is a later
step, not the one that removed the walker.
"""

from __future__ import annotations


def should_promote_to_workspace(*, explicit_flag: bool = False) -> bool:
    """Apply the structural gate.

    A surface is promoted to the workspace feed when the turn explicitly flags
    it (``explicit_flag=True``). Everything else stays chat-only until the gate
    is rewired to the turn's real output.
    """
    return explicit_flag
