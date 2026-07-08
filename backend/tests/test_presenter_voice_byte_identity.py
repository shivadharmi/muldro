"""Step 7B1 P4: PRESENTER_VOICE extraction must be byte-neutral.

``PRESENTER_VOICE`` (the reusable formatting + surface-emission fragment) is extracted
from ``PRESENTER_PROMPT`` so the deep runtime can append it as an inline-format
augmentation. The legacy Presenter agent still renders ``PRESENTER_PROMPT`` verbatim, so
the extraction MUST NOT change a single byte of it — this golden-hash test is load-bearing.

The golden hash was captured from the pre-refactor ``PRESENTER_PROMPT`` at HEAD:
    uv run python -c "from src.orchestrator.prompts import PRESENTER_PROMPT; \\
        import hashlib; print(hashlib.sha256(PRESENTER_PROMPT.encode()).hexdigest())"
"""

import hashlib

from src.orchestrator.prompts import PRESENTER_PROMPT, PRESENTER_VOICE

# Golden sha256 of PRESENTER_PROMPT captured BEFORE the PRESENTER_VOICE extraction.
_PRESENTER_PROMPT_GOLDEN_SHA256 = "65461ec6046574cb1804db2c11c57ff03689fd7461da0939e908052c8d782e2b"


def test_presenter_prompt_byte_identical_after_voice_extraction():
    """The refactor must leave PRESENTER_PROMPT byte-for-byte unchanged."""
    actual = hashlib.sha256(PRESENTER_PROMPT.encode()).hexdigest()
    assert actual == _PRESENTER_PROMPT_GOLDEN_SHA256, (
        "PRESENTER_PROMPT bytes changed during PRESENTER_VOICE extraction — "
        f"expected {_PRESENTER_PROMPT_GOLDEN_SHA256}, got {actual}"
    )


def test_presenter_voice_is_substring_of_presenter_prompt():
    """The extracted fragment must be the exact contiguous block still embedded in
    PRESENTER_PROMPT (rules + surface-generation guidance)."""
    assert PRESENTER_VOICE in PRESENTER_PROMPT
    # The fragment is the reusable voice, NOT the Presenter-specific role or examples.
    assert PRESENTER_VOICE.startswith("<rules>")
    assert PRESENTER_VOICE.endswith("</surface_generation>")
    assert "<role>" not in PRESENTER_VOICE
    assert "<examples>" not in PRESENTER_VOICE
