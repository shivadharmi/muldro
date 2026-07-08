"""Step 7C P1.2: deep-runtime inline read-back verifier middleware.

The middleware is a ``@wrap_tool_call`` interceptor placed INNER of write_lock, OUTER of the
jarvis_tool_dispatcher. It runs the write via ``handler(request)`` (the dispatcher executes the
tool and returns a bare ``ToolMessage``), then — for an irreversible/external write — reads the
effect back and ANNOTATES the verdict onto a content-JSON key (NEVER ``status``, so the SSE
frame does not flip to ``blocked``). CONTRADICTED → an escalate-first divergence payload (the
compensator is offered, never auto-run). CONFIRMED + a gated ``authorization_source`` → the
injected trust-increment.

The decorated hook is exposed on the built ``AgentMiddleware`` as ``awrap_tool_call`` — the same
invocation the write_lock / trust_gate tests use: ``mw.awrap_tool_call(request, handler)``.

Pure/offline: no live API, no DB, no Redis. ``read_fn`` / ``record_confirmed_outcome`` are
mocked; ``resolve_capability`` / ``assess_risk`` are plain closures returning ``_async(...)``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import ToolMessage

from src.deep_runtime.middleware.readback import make_readback_middleware
from src.services.verification import post_conditions
from src.services.verification.post_conditions import PostCondition

# ── local helpers ────────────────────────────────────────────────────────────


def _async(x):
    """An already-completed coroutine yielding ``x`` (a resolve/assess closure return)."""

    async def _coro():
        return x

    return _coro()


class _Risk:
    """A tiny RiskAssessment stand-in exposing only the attrs the verifier reads."""

    def __init__(self, *, reversible=False, blast_radius="external_single", risk_level="high"):
        self.reversible = reversible
        self.blast_radius = blast_radius
        self.risk_level = risk_level


def _request(name, args=None, call_id="tc1"):
    """Minimal ToolCallRequest stand-in: only ``.tool_call`` is read."""
    return SimpleNamespace(tool_call={"name": name, "args": args or {}, "id": call_id})


def _hook(mw):
    """Extract the async wrap-tool-call hook bound on the middleware instance."""
    return mw.awrap_tool_call


def _handler_returning(msg):
    """An async inner-handler stand-in returning a fixed dispatcher result."""

    async def handler(request):  # noqa: ANN001, ARG001
        return msg

    return handler


def _mw(*, resolve, assess=None, read_fn=None, record=None, source="autonomous"):
    return make_readback_middleware(
        workspace_id="ws1",
        authorization_source=source,
        resolve_capability=resolve,
        assess_risk=assess or (lambda cap, args: _async(_Risk())),
        read_fn=read_fn,
        record_confirmed_outcome=record,
    )


def _fake_pc(assertion_result: bool) -> PostCondition:
    """A fake post-condition whose assertion returns a fixed truth value."""
    return PostCondition(
        read_capability="fake.read",
        read_args=lambda write_input, write_output: {"probe": True},
        assertion=lambda read_result, write_input, write_output: assertion_result,
    )


# ── tests ────────────────────────────────────────────────────────────────────


async def test_unverified_annotates_and_does_not_block():
    """An irreversible cap with NO deterministic read (read_fn=None + UNVERIFIABLE) resolves to
    UNVERIFIED — the annotation lands, status stays non-error (never blocked)."""
    msg = ToolMessage(content="ok", tool_call_id="tc1", name="send_email")
    mw = _mw(resolve=lambda n: _async("email.send"), read_fn=None)
    out = await _hook(mw)(_request("send_email"), _handler_returning(msg))
    body = json.loads(out.content)
    assert body["verification"]["verdict"] == "unverified"
    assert out.status != "error"


async def test_builtin_falls_through():
    """A deepagents built-in (write_todos) is returned unchanged BEFORE any resolve/verify."""
    msg = ToolMessage(content="done", tool_call_id="tc1", name="write_todos")
    seen = {"resolve": False}

    def resolve(n):
        seen["resolve"] = True
        return _async("email.send")

    mw = _mw(resolve=resolve)
    out = await _hook(mw)(_request("write_todos"), _handler_returning(msg))
    assert out is msg  # untouched object — no re-copy, no annotation
    assert seen["resolve"] is False


async def test_error_result_passthrough():
    """A status='error' dispatcher result (blocked/contended/failed write) is passed through
    unchanged — nothing to verify, resolve_capability is never called."""
    err = ToolMessage(
        content=json.dumps({"error": "boom", "blocked": True}),
        tool_call_id="tc1",
        name="send_email",
        status="error",
    )
    seen = {"resolve": False}

    def resolve(n):
        seen["resolve"] = True
        return _async("email.send")

    mw = _mw(resolve=resolve)
    out = await _hook(mw)(_request("send_email"), _handler_returning(err))
    assert out is err
    assert seen["resolve"] is False


async def test_read_only_capability_skipped():
    """A read-only capability is never read-back-verified — risk is not even assessed."""
    msg = ToolMessage(content="msgs", tool_call_id="tc1", name="list_email")
    seen = {"assess": False}

    def assess(cap, args):
        seen["assess"] = True
        return _async(_Risk())

    mw = _mw(resolve=lambda n: _async("email.read"), assess=assess)
    out = await _hook(mw)(_request("list_email"), _handler_returning(msg))
    assert out is msg
    assert "verification" not in json.dumps(out.content)
    assert seen["assess"] is False


async def test_reversible_internal_confirmed_trivially():
    """A reversible-internal write (email.draft) resolves to CONFIRMED WITHOUT a read — the
    verifier short-circuits before the read for not-verification-required caps."""
    msg = ToolMessage(
        content=json.dumps({"draft_id": "d1"}), tool_call_id="tc1", name="draft_email"
    )
    read_fn = AsyncMock()
    reversible_internal = _Risk(reversible=True, blast_radius="internal", risk_level="low")
    mw = _mw(
        resolve=lambda n: _async("email.draft"),
        assess=lambda c, a: _async(reversible_internal),
        read_fn=read_fn,
    )
    out = await _hook(mw)(_request("draft_email"), _handler_returning(msg))
    body = json.loads(out.content)
    assert body["verification"]["verdict"] == "confirmed"
    read_fn.assert_not_awaited()  # short-circuited before the read seam


async def test_contradicted_annotates_escalation_not_blocked(monkeypatch):
    """A read-back that runs and finds the effect ABSENT → CONTRADICTED: an escalate-first
    divergence payload is annotated, but status stays non-error (surfaced, never blocked)."""
    fake_cap = "fake.contradict"
    monkeypatch.setitem(post_conditions.POST_CONDITIONS, fake_cap, _fake_pc(False))
    read_fn = AsyncMock(return_value=[])
    msg = ToolMessage(content=json.dumps({"id": "x1"}), tool_call_id="tc1", name="do_write")
    mw = _mw(resolve=lambda n: _async(fake_cap), read_fn=read_fn)
    out = await _hook(mw)(_request("do_write"), _handler_returning(msg))
    body = json.loads(out.content)
    assert body["verification"]["verdict"] == "contradicted"
    assert body["verification"]["escalation"]["capability"] == fake_cap
    assert out.status != "error"
    read_fn.assert_awaited_once()


async def test_confirmed_gated_fires_increment(monkeypatch):
    """A read-back that confirms the effect + a GATED authorization_source (autonomous) fires the
    injected trust-increment once, with the resolved capability + risk_level."""
    fake_cap = "fake.confirm"
    monkeypatch.setitem(post_conditions.POST_CONDITIONS, fake_cap, _fake_pc(True))
    read_fn = AsyncMock(return_value=[{"id": "x1"}])
    record = AsyncMock()
    msg = ToolMessage(content=json.dumps({"id": "x1"}), tool_call_id="tc1", name="do_write")
    mw = _mw(
        resolve=lambda n: _async(fake_cap),
        assess=lambda c, a: _async(_Risk(risk_level="medium")),
        read_fn=read_fn,
        record=record,
        source="autonomous",
    )
    out = await _hook(mw)(_request("do_write"), _handler_returning(msg))
    body = json.loads(out.content)
    assert body["verification"]["verdict"] == "confirmed"
    record.assert_awaited_once()
    _, kwargs = record.await_args
    assert kwargs["capability"] == fake_cap
    assert kwargs["risk_level"] == "medium"


async def test_confirmed_direct_chat_does_NOT_increment(monkeypatch):  # noqa: N802
    """Same CONFIRMED verdict but a direct_user_request source (ungated) MUST NOT increment —
    the is_gated_source guard (negative control with teeth). The verdict still annotates."""
    fake_cap = "fake.confirm2"
    monkeypatch.setitem(post_conditions.POST_CONDITIONS, fake_cap, _fake_pc(True))
    read_fn = AsyncMock(return_value=[{"id": "x1"}])
    record = AsyncMock()
    msg = ToolMessage(content=json.dumps({"id": "x1"}), tool_call_id="tc1", name="do_write")
    mw = _mw(
        resolve=lambda n: _async(fake_cap),
        read_fn=read_fn,
        record=record,
        source="direct_user_request",
    )
    out = await _hook(mw)(_request("do_write"), _handler_returning(msg))
    body = json.loads(out.content)
    assert body["verification"]["verdict"] == "confirmed"
    record.assert_not_awaited()
