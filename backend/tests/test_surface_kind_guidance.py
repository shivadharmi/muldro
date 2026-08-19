"""What the Presenter is told about surface kinds must match what SurfaceSpec accepts.

`SurfaceSpec.kind` is typed `SurfaceKind`, a strict Literal — so any kind the prompt fails to
forbid is a kind the model can successfully emit.

Every assertion parses the prompt's STRUCTURE (the markdown table rows, the "Do NOT use these
kinds" bullet list) rather than searching for a bare substring. Measured: `"run"`,
`"approval"`, `"proactive_insight"`, `"message"` and `"plan"` all occur as substrings of
ordinary prose in this prompt, so a substring assertion passes before and after the fix and
proves nothing.
"""

from __future__ import annotations

import re
import typing

from src.contracts import SurfaceSpec
from src.orchestrator.prompts import PRESENTER_VOICE

# Kinds only the system may create. `prepared_work` is the review queue for actions staged on
# turns with no human present, and settled decision D2 makes it the ONLY place such an action
# can be acted on — a second one authored by an agent would split that queue.
#
# Deliberately NOT derived from `src.ui.contracts.SYSTEM_SURFACE_KINDS`: that frozenset means
# "has a detail API" and includes `summary`/`briefing`/`alert`/`recommendation`, which the
# Presenter is legitimately offered. The two concepts differ.
SYSTEM_ONLY = {"approval", "proactive_insight", "prepared_work", "run"}

_FORBIDDEN_BLOCK_RE = re.compile(r"Do NOT use these kinds.*?(?=\n\n)", re.S)


def _valid_kinds() -> set[str]:
    return set(typing.get_args(SurfaceSpec.model_fields["kind"].annotation))


def _offered_kinds() -> set[str]:
    """Kinds named in the prompt's markdown kind table."""
    rows = set(re.findall(r"^\| (\w+) +\|", PRESENTER_VOICE, re.M))
    assert rows, "expected a markdown kind table in the prompt"
    return rows - {"Kind"}


def _forbidden_kinds() -> set[str]:
    """Kinds bulleted under the 'Do NOT use these kinds' heading."""
    match = _FORBIDDEN_BLOCK_RE.search(PRESENTER_VOICE)
    assert match, "expected a 'Do NOT use these kinds' block in the prompt"
    return set(re.findall(r"^- (\w+)", match.group(0), re.M))


def test_every_system_only_kind_is_explicitly_forbidden():
    missing = sorted((SYSTEM_ONLY & _valid_kinds()) - _forbidden_kinds())
    assert missing == [], f"{missing} are accepted by SurfaceSpec but the prompt never forbids them"


def test_the_message_kind_is_offered():
    """`message` is the Presenter-authored kind and has its own promotion path in
    surface_pusher; the prompt's table never listed it."""
    assert "message" in _offered_kinds()


def test_every_kind_the_prompt_offers_is_actually_valid():
    """Teeth in the other direction: the prompt must not teach a kind SurfaceSpec rejects."""
    unknown = sorted(_offered_kinds() - _valid_kinds())
    assert unknown == [], f"prompt offers kinds SurfaceSpec would reject: {unknown}"


def test_every_kind_the_prompt_forbids_is_actually_valid():
    """A forbidden kind that SurfaceSpec does not accept is stale guidance — it spends the
    model's attention forbidding something that could never have been emitted."""
    unknown = sorted(_forbidden_kinds() - _valid_kinds())
    assert unknown == [], f"prompt forbids kinds SurfaceSpec does not define: {unknown}"


def test_the_prompt_never_both_offers_and_forbids_a_kind():
    """The two lists are the prompt's answer to one question; an overlap is a contradiction
    the model has to resolve on its own."""
    both = sorted(_offered_kinds() & _forbidden_kinds())
    assert both == [], f"prompt both offers and forbids: {both}"
