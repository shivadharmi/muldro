"""Write one Unit's body, and check it against its kind's budget.

User-facing prose is the Presenter's job, and the Presenter already writes it
through `complete_text`; this is that same seam, sized for one card. It is a
plain utility completion, not an agent: there are no tools, no gates and no
turn, so it carries no MULDRO_SOUL_CORE - every behavioural law in that prompt
is about tools and gates, and it would cost its tokens on every unit of every
poll.

TIER. `fast` (Haiku by default) - one call per unit per poll is the dominant
cost in this design, and summarising at most three short messages is well
inside a fast model. Validating the result against the kind's budget turns
budget compliance into a CHECKED property rather than a bet on model quality,
which is what makes the cheap tier defensible. If prose quality proves to be
the binding constraint, BODY_TIER is the one line to change.
"""

import logging
from collections.abc import Sequence

from src.llm.utility import complete_text_with_usage

# view -> orchestrator, deliberately. `record_token_span` is a leaf recorder
# that opens its own short-lived session and never raises; `src/services/triage.py`
# imports it the same way, and `src/view/ranking/build.py` already reaches into
# `src/services`. The alternative - returning usage up to the caller - would put
# cost accounting in three call sites instead of one.
from src.orchestrator.budget import record_token_span
from src.view.body import LEDE_BUDGETS, validate_body
from src.view.body_prompt import BODY_SYSTEM_PROMPT, build_body_request
from src.view.contracts import Frame, Quote

logger = logging.getLogger(__name__)

BODY_TIER = "haiku"
BODY_MAX_TOKENS = 700
# Non-zero on purpose. A repair re-prompt already differs from the first (it
# names the overrun), but a fully deterministic decode is one more way for
# three attempts to produce the same too-long paragraph three times.
BODY_TEMPERATURE = 0.4


class BodyUnavailable(RuntimeError):  # noqa: N818 - names the outcome, not an error mode
    """The model could not be reached, or answered with nothing.

    TRANSIENT. The caller must NOT persist anything on this - the next poll
    retries. Distinct from a budget give-up, which is deterministic for this
    event set and IS persisted so it is not retried forever.
    """


async def generate_body(frame: Frame, quotes: Sequence[Quote], *, workspace_id: str) -> str:
    """Return validated markdown for one Unit, or "" when the model gave up.

    Raises `BodyUnavailable` on a transient failure. See the class docstring
    for why the two are not the same outcome.
    """
    if frame.kind not in LEDE_BUDGETS:
        # `validate_body` would raise "unknown frame kind", which is NOT
        # something a model can repair - it would burn three calls to produce
        # nothing. Fail here, before spending anything.
        logger.error(
            "view_body_unknown_kind kind=%s key=%s",
            frame.kind,
            frame.key,
            extra={"kind": frame.kind, "frame_key": frame.key},
        )
        return ""

    request = build_body_request(frame, quotes)
    text, _usage = await _complete(request, workspace_id=workspace_id)
    if not text:
        raise BodyUnavailable(f"empty completion for {frame.key}")
    return validate_body(text, frame.kind)


async def _complete(user: str, *, workspace_id: str) -> tuple[str, object]:
    """One utility completion, with its cost recorded. Raises BodyUnavailable."""
    try:
        text, usage = await complete_text_with_usage(
            system=BODY_SYSTEM_PROMPT,
            user=user,
            tier=BODY_TIER,
            max_tokens=BODY_MAX_TOKENS,
            temperature=BODY_TEMPERATURE,
            workspace_id=workspace_id,
        )
    except Exception as exc:  # noqa: BLE001 - every provider error is transient here
        raise BodyUnavailable(str(exc)) from exc
    # A direct utility call bypasses the deep-runtime budget middleware, so the
    # span is recorded by hand or the cost is invisible. Best-effort; never raises.
    await record_token_span(
        agent_name="view_body",
        model=usage.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        trigger="perception",
        workspace_id=workspace_id,
    )
    return text.strip(), usage
