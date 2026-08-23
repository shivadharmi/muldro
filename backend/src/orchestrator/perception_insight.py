"""What a non-actionable perception cycle may publish to the founder.

Two independent questions, deliberately separate. `_publishes_insights` asks
whether this CYCLE has anything cross-cutting to say at all;
`_is_publishable_insight` asks whether a particular goal string is prose rather
than a debug artefact. Conflating them produced both of the failures this module
records: a single-source poll publishing a restatement of its own signal, and a
raw JSON blob arriving as a card whose headline was "{".

Extracted from perception_runner.py, which is over its size cap and must shrink
rather than be re-recorded.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# A Planner response that would not parse falls back to `PlanOutput(goal=
# response_text, ...)` in `extract_plan` — the ENTIRE raw model output, kept
# as a diagnostic. That is fine internally and it is already logged, but this
# branch turns a goal into prose the founder reads, and a raw JSON blob
# arrived as a card whose headline was "{".
#
# So the boundary checks the shape rather than trusting the field: an insight
# is a sentence muldro wrote for a person. Deliberately NOT a JSON parse — a
# fragment, a code fence or a truncated dump is just as unreadable as valid
# JSON, and all of them start by announcing themselves.
_NOT_PROSE_PREFIXES = ("{", "[", "```", '"goal"', "goal:")

# Long enough to be a claim. The fallback dumps hundreds of characters, so this
# only rejects the other end: a bare token that says nothing.
_MIN_INSIGHT_CHARS = 12


# The one perception "source" that is not a connector: the cross-source
# synthesis pass, which correlates 2+ sources in a single tick.
SYNTHESIS_SOURCE = "synthesis"


def _publishes_insights(source: str) -> bool:
    """Whether this cycle may turn a non-actionable plan goal into a finding.

    Only synthesis may. The escape hatch below exists because the synthesis path
    has no prior relevance-routing step, so a genuine cross-cutting observation
    would otherwise be discarded silently. A SINGLE-source poll has no
    cross-cutting anything — and when it finds nothing actionable, the Planner's
    goal is a restatement of the signal it was handed. That is how the founder's
    feed came to hold "You have 1 new Gmail event. Without information about
    sender, subject, or calendar impact, ..." — the model reporting that it
    knows nothing, rendered as a finding.

    Gating on the path rather than on the prose is deliberate. A blocklist of
    hedging phrases is a losing game against a model that can rephrase, and it
    would also reject a real insight that happened to be tentative. The
    structural fact is that a source with nothing actionable has nothing to say.
    """
    return source == SYNTHESIS_SOURCE


def _is_publishable_insight(goal: str | None) -> bool:
    """Whether a plan goal is prose a human should be shown."""
    text = (goal or "").strip()
    if len(text) < _MIN_INSIGHT_CHARS:
        return False
    if text.startswith(_NOT_PROSE_PREFIXES):
        return False
    # A dump that opens with prose and then carries structure is still a dump.
    return '"steps"' not in text and '"capability"' not in text


# Also lives here rather than on PerceptionRunner: parsing a planner block is a
# pure text operation on the same "what did the model actually say" boundary
# the predicates above police, and the runner is over its size cap.
def extract_perception_policy(planner_text: str):
    """Parse a perception_policy JSON block from planner output, if present."""
    import json
    import re

    from src.contracts import PerceptionDecision

    if not planner_text or "perception_policy" not in planner_text:
        return None

    try:
        # The block may be embedded in markdown, so find it rather than parse
        # the whole response.
        match = re.search(r'"perception_policy"\s*:\s*(\{[^}]+\})', planner_text)
        if not match:
            return None
        return PerceptionDecision(**json.loads(match.group(1)))
    except Exception:
        logger.debug("Failed to parse perception_policy from planner", exc_info=True)
        return None
