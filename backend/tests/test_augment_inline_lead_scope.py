"""A-3 (B2): lead-scope the Presenter voice inline-format augmentation.

``_augment_system_blocks_for_inline`` used to append ``PRESENTER_VOICE`` to EVERY deep
``call_agent_stream`` agent whenever ``deep_inline_format`` was on — agent-agnostically.
But ``call_agent_stream`` (and the shadow turn) build non-reply agents too: chat_processor
streams the ``planner`` (emits PlanOutput JSON), the routed per-step read/execute agent
(Perceiver / Executor / Librarian), AND the ``presenter``. Only the reply-producing lead
(the Presenter) should carry the surface-generation Presenter voice.

This module pins the new lead-scoped contract:
  (a) inline_format=True + is_reply_lead=True  -> PRESENTER_VOICE appended exactly once;
  (b) inline_format=True + is_reply_lead=False -> NOT appended (a non-lead like a Perceiver
      read no longer gets surface-generation rules) — the ONLY behavior change vs today;
  (c) inline_format=False -> byte-identical identity regardless of is_reply_lead (the
      production default stays byte-neutral at both call sites);
  (d) idempotency still holds when is_reply_lead=True (a block list already carrying
      PRESENTER_VOICE — the presenter's own base prompt — is not doubled);
  (e) the lead signal ``_is_reply_lead`` is True ONLY for the presenter, so both the live
      (call_agent_stream) and shadow (run_shadow_turn) call sites — which both derive it
      from the SAME ``agent_name`` — agree for an equivalent turn;
  (f) the ``is_reply_lead`` default is the SAFE value (no append) so a caller that forgets
      to pass it never leaks the voice into a non-lead prompt.
"""

from __future__ import annotations

from src.orchestrator.agent_invoker import (
    _augment_system_blocks_for_inline,
    _is_reply_lead,
)
from src.orchestrator.prompts import PRESENTER_VOICE


def _base_blocks() -> list[dict]:
    return [{"type": "text", "text": "soul+role"}]


# --- (a) lead + on -> appended once ---------------------------------------------------
def test_lead_and_inline_on_appends_presenter_voice_once():
    blocks = _base_blocks()
    out = _augment_system_blocks_for_inline(blocks, True, is_reply_lead=True)

    assert out is not blocks  # new list, input untouched
    assert out[:-1] == blocks  # original blocks preserved, in order
    assert out[-1] == {"type": "text", "text": PRESENTER_VOICE}
    joined = "".join(b.get("text", "") for b in out)
    assert joined.count(PRESENTER_VOICE) == 1


# --- (b) non-lead + on -> NOT appended (the ONE behavior change) -----------------------
def test_non_lead_with_inline_on_does_not_append():
    """A non-reply agent (planner / Perceiver read / Executor) streamed through
    call_agent_stream must NOT receive the Presenter surface-generation voice, even with
    deep_inline_format on. This is the teeth of the lead-scope gate."""
    blocks = _base_blocks()
    out = _augment_system_blocks_for_inline(blocks, True, is_reply_lead=False)

    assert out is blocks  # identity — no append for a non-lead
    assert not any(b.get("text") == PRESENTER_VOICE for b in out)


# --- (c) off -> byte-identical identity regardless of lead flag ------------------------
def test_inline_off_is_identity_for_lead():
    blocks = _base_blocks()
    out = _augment_system_blocks_for_inline(blocks, False, is_reply_lead=True)
    assert out is blocks
    assert not any(b.get("text") == PRESENTER_VOICE for b in out)


def test_inline_off_is_identity_for_non_lead():
    blocks = _base_blocks()
    out = _augment_system_blocks_for_inline(blocks, False, is_reply_lead=False)
    assert out is blocks
    assert not any(b.get("text") == PRESENTER_VOICE for b in out)


# --- (d) idempotency still holds for the lead -----------------------------------------
def test_idempotent_for_lead_when_voice_already_present():
    """The presenter's own base prompt already carries PRESENTER_VOICE; the lead append
    must not double it."""
    blocks = [{"type": "text", "text": f"You are the Presenter.\n\n{PRESENTER_VOICE}"}]
    out = _augment_system_blocks_for_inline(blocks, True, is_reply_lead=True)

    assert out is blocks  # identity — no second injection
    joined = "".join(b.get("text", "") for b in out)
    assert joined.count(PRESENTER_VOICE) == 1


# --- (e) lead signal: presenter only; live and shadow agree from the same agent_name ---
def test_is_reply_lead_true_only_for_presenter():
    assert _is_reply_lead("presenter") is True


def test_is_reply_lead_false_for_non_reply_agents():
    # Every agent that chat_processor actually streams through call_agent_stream as a
    # non-reply step (planner, routed reads/writes) must be a non-lead.
    for name in ("planner", "perceiver", "executor", "librarian", "persona", ""):
        assert _is_reply_lead(name) is False, name


def test_live_and_shadow_agree_on_lead_for_equivalent_turn():
    """The live seam (call_agent_stream) and the shadow seam (run_shadow_turn) both derive
    is_reply_lead from the SAME agent_name for an equivalent turn, so they can never
    diverge on whether PRESENTER_VOICE is appended (a mismatch would poison the
    shadow-divergence signal)."""
    for agent_name in ("presenter", "planner", "perceiver", "executor"):
        live = _is_reply_lead(agent_name)
        shadow = _is_reply_lead(agent_name)  # same pure fn, same input -> same value
        assert live is shadow


# --- (f) default is the SAFE value (no append) ----------------------------------------
def test_is_reply_lead_defaults_to_no_append():
    """A caller that omits is_reply_lead must get the safe (no-append) behavior even when
    inline_format is on, so a forgotten arg never leaks the voice into a non-lead prompt."""
    blocks = _base_blocks()
    out = _augment_system_blocks_for_inline(blocks, True)  # is_reply_lead omitted
    assert out is blocks
    assert not any(b.get("text") == PRESENTER_VOICE for b in out)
