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

# A headline is plain text. These patterns are markdown constructs or bare URLs
# that a markdown renderer would turn into emphasis or a live link. An email
# subject reaching this field is the phishing vector described in spec §1, so
# the type refuses them rather than trusting a caller to sanitize.
_MARKDOWN_IN_HEADLINE = re.compile(
    r"""
      \*\*            # bold
    | (?<!\w)_[^_]+_  # underscore emphasis
    | \[[^\]]*\]\(    # link
    | ^\s*\#          # heading
    | `               # code span
    | https?://       # bare URL (remark-gfm autolinks these)
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
    """Built by code from a domain row. No field here is model-authored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    group_key: str | None = None
    kind: FrameKind
    status: FrameStatus
    headline: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1)
    entity_type: str = ""
    occurred_at: datetime
    updated_at: datetime
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    event_count: int = Field(default=1, ge=1)
    affordances: list[Affordance] = Field(default_factory=list)

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
    quotes: list[Quote] = Field(default_factory=list)
