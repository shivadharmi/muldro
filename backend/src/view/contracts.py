"""Typed contracts for the view layer.

`Frame` is built by code from a domain row; no field on it is model-authored.
`body` is the model's entire contract — one markdown field. `Quote` carries
external text verbatim with its attribution, and is the ONLY route by which
external text reaches the screen.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FrameKind = Literal["proposal", "finding", "run", "record", "briefing"]
FrameStatus = Literal["needs_you", "scheduled", "running", "done", "failed", "new", "seen"]

# `Frame.headline`'s upper bound, named once and imported by frame.py so the
# clamp and the field cannot drift apart. This is a TYPE SANITY bound, not a
# display rule: spec §4.2 gives the headline CSS `line-clamp-2`, which at 13px
# in a 320px cell shows on the order of 80 characters, so 200 is invisible in
# practice and exists only to keep the field finite. It is deliberately NOT one
# of the seven truncation rules §2.3 replaces - those are about what the reader
# sees, and CSS decides that.
MAX_HEADLINE_CHARS = 200

# A headline is plain text, line-clamped, and never passed to a markdown
# renderer. These alternatives refuse everything remark-gfm would turn into
# emphasis, strikethrough, a heading or a live link — including all three GFM
# autolink forms (bare https?://, www., and bare email) and the CommonMark
# <scheme:...> protocol autolink — plus raw newlines (which close setext
# headings and lists on their own) and control/bidi-override characters that
# can spoof plain text with no markdown involved at all. An email subject
# reaching this field is the phishing vector described in spec §1, so the
# type refuses these constructs rather than trusting a caller to sanitize.
_MARKDOWN_IN_HEADLINE = re.compile(
    r"""
      \*\*                       # bold
    | (?<!\w)\*[^*]+\*           # single-asterisk emphasis
    | (?<!\w)_[^_]+_             # underscore emphasis
    | ~~                         # strikethrough
    | \[[^\]]*\]\(               # inline link
    | ^\s*\#                     # heading
    | `                          # code span
    | https?://                  # bare URL autolink
    | www\.                      # GFM www autolink
    | <[a-zA-Z][a-zA-Z0-9+.-]*:  # CommonMark protocol autolink, e.g. <mailto:
    | \S+@\S+\.\S+               # GFM email autolink
    | \n                         # newline: closes setext headings / lists
    | [\x00-\x1f\x7f-\x9f]       # C0/C1 control characters
    | [\u202a-\u202e\u2066-\u2069]  # bidi overrides / isolates (e.g. RLO can reverse a headline)
    """,
    re.VERBOSE | re.MULTILINE,
)


class Affordance(BaseModel):
    """A thing the founder can do. Both fields are code-authored.

    `capability` must name an entry in CAPABILITY_CATALOG; `label` is written
    in code. The model may argue for an action in its body but cannot mint one
    — a free-string capability is how the founder came to be offered "Mark new
    email as unread" on an email nobody had read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=40)
    variant: Literal["primary", "secondary"] = "secondary"


class Frame(BaseModel):
    """Built by code from a domain row. No field here is model-authored.

    Callers construct a Frame from raw external data (e.g. an email subject)
    via `frame_for_event` (arriving in Task 3), which neutralizes external
    text before it ever reaches `headline`. The validator below also refuses
    ordinary, non-malicious strings — e.g. "Fix `parse_url` crash" or
    "PR #22: refactor _internal_ cache" — and a refusal means the founder
    sees no card at all, so a caller that skips neutralization risks silently
    losing cards, not spoofing them.

    The plain-text guarantee holds for normal construction only:
    `Frame.model_construct(...)` and `frame.model_copy(update={...})` both
    bypass validation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    group_key: str | None = None
    kind: FrameKind
    status: FrameStatus
    headline: str = Field(min_length=1, max_length=MAX_HEADLINE_CHARS)
    source: str = Field(min_length=1)
    entity_type: str = ""
    occurred_at: datetime
    updated_at: datetime
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    event_count: int = Field(default=1, ge=1)
    affordances: tuple[Affordance, ...] = Field(default_factory=tuple)

    @field_validator("headline")
    @classmethod
    def _plain_text_only(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("frame.headline must not be blank")
        if _MARKDOWN_IN_HEADLINE.search(stripped):
            raise ValueError(
                "frame.headline is plain text; markdown and bare URLs are refused "
                "(spec §10 invariant 2)"
            )
        return stripped


class Quote(BaseModel):
    """External text. Copied by code, never interpolated into `body`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1)
    who: str = Field(min_length=1)
    when: datetime


class Unit(BaseModel):
    """What the renderer receives. The only object in the view layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    frame: Frame
    body: str = ""
    quotes: tuple[Quote, ...] = Field(default_factory=tuple)
