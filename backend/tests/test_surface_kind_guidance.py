"""What the lead is offered for a surface kind must match what `SurfaceSpec` accepts.

This rule used to live in prose: `PRESENTER_VOICE` carried a markdown kind table and a "Do NOT
use these kinds" bullet list, and these tests parsed that structure. Both are deleted — the
taxonomy moved into the `render_surface` tool's input schema, where `RenderSurfaceInput.kind` is
a Literal narrower than `SurfaceKind`. So the same rule is now enforced by a type rather than by
a paragraph, and these assertions follow it there.

The move makes every one of them structural. The offered set is the Literal's args; the
forbidden set is `SurfaceKind` minus the Literal — there is no second list to fall out of sync,
so "offers and forbids the same kind" is impossible by construction and the partition holds by
definition. What is still worth pinning is that the partition falls in the RIGHT PLACE: that no
system-only kind leaked into the Literal, that `message` is offered, and that a newly added
`SurfaceKind` gets classified deliberately rather than by default.
"""

from __future__ import annotations

import typing

from src.contracts import SurfaceSpec
from src.tools.schemas import RenderSurfaceInput

# Kinds only the system may create. `prepared_work` is the review queue for actions staged on
# turns with no human present, and settled decision D2 makes it the ONLY place such an action
# can be acted on — a second one authored by an agent would split that queue.
#
# Deliberately NOT derived from `src.ui.contracts.SYSTEM_SURFACE_KINDS`: that frozenset means
# "has a detail API" and includes `summary`/`briefing`/`alert`/`recommendation`, which the
# lead is legitimately offered. The two concepts differ.
SYSTEM_ONLY = {"approval", "proactive_insight", "prepared_work", "run"}

# Kinds that are neither system-only nor offered: still accepted by `SurfaceSpec` for rows
# already in the database, but nothing new should be authored with them. `derive_surface_kind`
# still produces `plan`; the tool must not.
LEGACY = {"plan"}


def _valid_kinds() -> set[str]:
    return set(typing.get_args(SurfaceSpec.model_fields["kind"].annotation))


def _offered_kinds() -> set[str]:
    """Kinds the `render_surface` tool will accept — its `kind` Literal."""
    kinds = set(typing.get_args(RenderSurfaceInput.model_fields["kind"].annotation))
    assert kinds, "expected a closed Literal on RenderSurfaceInput.kind"
    return kinds


def _forbidden_kinds() -> set[str]:
    """Kinds the tool refuses: everything in `SurfaceKind` the Literal leaves out."""
    return _valid_kinds() - _offered_kinds()


def test_every_legacy_kind_is_excluded_from_the_tool():
    """A legacy kind must not be authorable — offering it would keep minting new rows in a
    shape the product has already moved off."""
    leaked = sorted((LEGACY & _valid_kinds()) & _offered_kinds())
    assert leaked == [], f"{leaked} are legacy but render_surface would accept them"


def test_every_system_only_kind_is_excluded_from_the_tool():
    leaked = sorted((SYSTEM_ONLY & _valid_kinds()) & _offered_kinds())
    assert leaked == [], f"{leaked} are system-only but render_surface would accept them"


def test_the_message_kind_is_offered():
    """`message` is the lead-authored kind and has its own promotion path in
    surface_pusher; the tool must offer it."""
    assert "message" in _offered_kinds()


def test_every_kind_the_tool_offers_is_actually_valid():
    """Teeth in the other direction: the tool must not accept a kind SurfaceSpec rejects.

    `RenderSurfaceInput.kind` and `SurfaceSpec.kind` are two independently written Literals;
    a kind offered by the first and rejected by the second is a surface built and then dropped.
    """
    unknown = sorted(_offered_kinds() - _valid_kinds())
    assert unknown == [], f"render_surface offers kinds SurfaceSpec would reject: {unknown}"


def test_the_tool_never_both_offers_and_forbids_a_kind():
    """Structural now: forbidden is defined as the complement of offered, so the two sets
    cannot overlap. Pinned anyway, because the day someone reintroduces a hand-written
    forbidden list is the day this can fail again."""
    both = sorted(_offered_kinds() & _forbidden_kinds())
    assert both == [], f"render_surface both offers and forbids: {both}"


def test_every_valid_kind_is_offered_system_only_or_legacy():
    """The Literal, `SYSTEM_ONLY` and `LEGACY` must together cover `SurfaceKind`, so a newly
    added kind cannot slip through unclassified.

    `SYSTEM_ONLY` above is hand-written — it has to be, because the codebase's own
    `SYSTEM_SURFACE_KINDS` means "has a detail API" and includes kinds the lead is
    legitimately offered. A hand-written set is a hole: add a system-only kind to `SurfaceKind`
    and nothing forces anyone to keep it out of the tool. This closes that hole from the other
    side. Every kind must be classified one way or the other, so a new one fails here until
    someone decides which it is.
    """
    unclassified = sorted(_valid_kinds() - (_offered_kinds() | SYSTEM_ONLY | LEGACY))
    assert unclassified == [], (
        f"{unclassified} are valid SurfaceKinds that render_surface neither offers nor "
        "SYSTEM_ONLY/LEGACY accounts for; classify each"
    )
