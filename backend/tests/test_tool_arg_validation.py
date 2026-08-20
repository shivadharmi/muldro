"""R1 survey: ToolExecutor parses tool arguments against TOOL_INPUT_MODELS — log-only.

``TOOL_INPUT_MODELS`` (src/tools/schemas.py) was consulted at startup by
``validate_registry()`` and nowhere else at call time, so an internal tool's typed
schema never actually constrained a call. ``render_surface`` is the sharp edge:
FastMCP only checks that ``sections`` is a ``list[dict]``, so ``{"type": "Bogus"}``
publishes cleanly and the frontend renders ``[Unknown: Bogus]``.

R1 adds the parse in SURVEY mode: every violation is logged with a greppable
``[toolargs]`` prefix, and NOTHING is rejected. These tests pin that property —
an invalid call is logged AND still dispatched — plus the ordering decision that
makes the parse safe: validation runs on the AGENT-supplied input, before
``_enrich_internal_input`` injects user_id/workspace_id. Those context args are
deliberately absent from the LLM-facing models, so validating after injection
would reject every internal call.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from src.models.tool_definitions import ToolDefinition
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID

_TOOLARGS_PREFIX = "[toolargs]"

# A valid render_surface payload, exactly as an agent would emit it: no user_id,
# no workspace_id — the dispatcher injects those AFTER validation.
_VALID_RENDER_SURFACE = {
    "kind": "message",
    "title": "Quarterly numbers",
    "sections": [
        {
            "id": "t1",
            "type": "Text",
            "properties": {"text": "Revenue is up.", "variant": "body"},
        }
    ],
}


def _make_tool_record(backend: str, *, server: str = "default", input_schema=None):
    """A double for a real ``ToolDefinition`` row.

    ``create_autospec`` rather than a bare MagicMock: the double must be able to be
    wrong in the same ways the real ORM object can (a typo'd attribute must raise,
    not silently return a truthy Mock).
    """
    tool = create_autospec(ToolDefinition, instance=True)
    tool.backend = backend
    tool.server = server
    tool.enabled = True
    tool.input_schema = input_schema
    return tool


def _make_tool_executor():
    from src.orchestrator.tool_executor import ToolExecutor

    events = MagicMock()
    events.publish_event = AsyncMock()

    mock_db = AsyncMock()
    db_factory = MagicMock()
    db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    db_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return ToolExecutor(events, lambda: db_factory)


async def _run_internal(tool_name: str, tool_input: dict, *, tool=None):
    """Dispatch an internal_mcp tool with the FastMCP round-trip doubled out.

    Returns ``(result, internal_call_double)`` so callers can assert on dispatch.
    """
    tool = tool or _make_tool_record("internal_mcp", server="intelligence")
    te = _make_tool_executor()
    # autospec off the real bound method: a call with the wrong arity fails here
    # exactly as it would against the real implementation.
    call_internal = create_autospec(te.call_internal_tool)
    call_internal.return_value = {"status": "ok"}
    te.call_internal_tool = call_internal

    mock_registry = AsyncMock()
    mock_registry.get_tool = AsyncMock(return_value=tool)
    with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
        result = await te.execute_tool(
            tool_name=tool_name,
            tool_input=tool_input,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )
    return result, call_internal


def _toolargs_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if _TOOLARGS_PREFIX in r.getMessage()]


@pytest.mark.asyncio
async def test_invalid_args_are_logged_but_still_dispatched(caplog):
    """The R1 safety property: a violation is reported, never enforced.

    ``sections`` carries a component type outside the AnyComponent union — the exact
    shape that renders as ``[Unknown: Bogus]`` — yet the call still reaches the tool.
    """
    caplog.set_level(logging.WARNING)
    bad_input = {
        **_VALID_RENDER_SURFACE,
        "sections": [{"id": "t1", "type": "Bogus", "properties": {"text": "x"}}],
    }

    result, call_internal = await _run_internal("render_surface", bad_input)

    logged = _toolargs_records(caplog)
    assert len(logged) == 1, f"expected exactly one [toolargs] warning, got {logged}"
    assert "render_surface" in logged[0].getMessage()

    call_internal.assert_awaited_once()
    assert result == {"status": "ok"}
    # The agent-supplied args reach the tool untouched apart from context injection.
    assert call_internal.await_args.args[1]["sections"] == bad_input["sections"]


@pytest.mark.asyncio
async def test_missing_required_field_is_logged_but_still_dispatched(caplog):
    """A structurally incomplete call (no ``title``) is surveyed, not blocked."""
    caplog.set_level(logging.WARNING)
    bad_input = {"kind": "message", "sections": []}

    result, call_internal = await _run_internal("render_surface", bad_input)

    assert len(_toolargs_records(caplog)) == 1
    call_internal.assert_awaited_once()
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_valid_call_logs_nothing(caplog):
    caplog.set_level(logging.WARNING)

    _, call_internal = await _run_internal("render_surface", dict(_VALID_RENDER_SURFACE))

    assert _toolargs_records(caplog) == []
    call_internal.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_args_are_not_required_by_the_input_model(caplog):
    """A valid agent payload WITHOUT the injected context args must validate cleanly.

    ``_VALID_RENDER_SURFACE`` carries no user_id/workspace_id, because the dispatcher
    injects them after the parse. If a context field were ever added to an input model
    as required, this fails — and every internal tool call would log a false violation.
    """
    caplog.set_level(logging.WARNING)
    assert "user_id" not in _VALID_RENDER_SURFACE
    assert "workspace_id" not in _VALID_RENDER_SURFACE

    _, call_internal = await _run_internal("render_surface", dict(_VALID_RENDER_SURFACE))

    assert _toolargs_records(caplog) == []
    # The context arg IS present by the time the tool is called. (Only user_id:
    # injection is signature-aware and render_surface's impl declares no workspace_id.)
    assert call_internal.await_args.args[1]["user_id"] == TEST_USER_ID


@pytest.mark.asyncio
async def test_validation_runs_on_the_pre_injection_input():
    """Pins the ORDERING decision, white-box, because black-box cannot see it.

    Every model in TOOL_INPUT_MODELS inherits pydantic's default ``extra="ignore"``, so
    an injected user_id/workspace_id is silently DROPPED rather than rejected — meaning
    a parse moved below ``_enrich_internal_input`` would look identical from the outside
    today and start rejecting the day any model tightens to ``extra="forbid"``. So this
    asserts on the input the parse actually received: it must be the agent's, not the
    dispatcher's enriched copy.
    """
    from src.orchestrator import tool_executor as te_mod

    seen: list[dict] = []

    def _record(tool_name: str, tool_input: dict):
        seen.append(dict(tool_input))
        return None

    spy = create_autospec(te_mod._validate_tool_input, side_effect=_record)
    with patch.object(te_mod, "_validate_tool_input", spy):
        await _run_internal("render_surface", dict(_VALID_RENDER_SURFACE))

    assert seen, "the parse never ran for an internal tool"
    assert "user_id" not in seen[0], (
        "the parse saw the dispatcher-enriched input — it must run BEFORE "
        "_enrich_internal_input, on the agent-supplied args"
    )
    assert "workspace_id" not in seen[0]


def test_no_input_model_declares_a_context_arg():
    """The other half of the ordering contract: the models stay LLM-facing.

    user_id/workspace_id are supplied by Muldro from auth/turn context, never invented
    by the model. A model that declared one would make the pre-injection parse wrong.
    """
    from src.orchestrator.tool_executor import _CONTEXT_ARGS
    from src.tools.schemas import TOOL_INPUT_MODELS

    offenders = {
        name: sorted(set(model.model_fields) & set(_CONTEXT_ARGS))
        for name, model in TOOL_INPUT_MODELS.items()
        if set(model.model_fields) & set(_CONTEXT_ARGS)
    }
    assert offenders == {}, f"input models declaring dispatcher-injected args: {offenders}"


@pytest.mark.asyncio
async def test_special_backend_passthrough_is_validated(caplog):
    """``report_governor_verdict`` has an input model and returns its input as-is.

    Keying on the model's existence rather than the backend is what covers it — the
    SPECIAL early-return sits below the parse.
    """
    caplog.set_level(logging.WARNING)
    tool = _make_tool_record("special", server="_special")
    te = _make_tool_executor()
    mock_registry = AsyncMock()
    mock_registry.get_tool = AsyncMock(return_value=tool)

    bad_input = {"verdict": "not_a_real_verdict", "risk_level": "low", "justification": "x"}
    with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
        result = await te.execute_tool(
            tool_name="report_governor_verdict",
            tool_input=bad_input,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )

    assert len(_toolargs_records(caplog)) == 1
    assert result == bad_input, "log-only: the passthrough still returns its input"


@pytest.mark.asyncio
async def test_tool_without_an_input_model_is_untouched(caplog):
    """External MCP tools have no entry in TOOL_INPUT_MODELS — nothing to parse."""
    caplog.set_level(logging.WARNING)
    tool = _make_tool_record("external_mcp", input_schema={"type": "object"})
    te = _make_tool_executor()
    mock_registry = AsyncMock()
    mock_registry.get_tool = AsyncMock(return_value=tool)
    mock_call_mcp = AsyncMock(return_value={"messages": []})

    with (
        patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry),
        patch("src.connectors.mcp_bridge.call_mcp_tool", mock_call_mcp),
    ):
        result = await te.execute_tool(
            tool_name="search_gmail_messages",
            tool_input={"anything": object()},
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )

    assert _toolargs_records(caplog) == []
    mock_call_mcp.assert_awaited_once()
    assert result == {"messages": []}


@pytest.mark.asyncio
async def test_composite_tool_without_a_model_is_untouched(caplog):
    """``web_search`` is composite and has no input model."""
    caplog.set_level(logging.WARNING)
    from src.tools.schemas import TOOL_INPUT_MODELS

    assert "web_search" not in TOOL_INPUT_MODELS

    tool = _make_tool_record("composite", server=None)
    te = _make_tool_executor()
    te.call_composite_tool = create_autospec(te.call_composite_tool)
    te.call_composite_tool.return_value = {"results": []}
    mock_registry = AsyncMock()
    mock_registry.get_tool = AsyncMock(return_value=tool)

    with patch("src.services.tool_registry.ToolRegistry", return_value=mock_registry):
        result = await te.execute_tool(
            tool_name="web_search",
            tool_input={"query": "muldro"},
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )

    assert _toolargs_records(caplog) == []
    assert result == {"results": []}


class TestRenderedError:
    """The rendered string is what R2 will RETURN, so it is pinned now."""

    def _render(self, tool_name: str, tool_input: dict) -> str:
        from src.orchestrator.tool_executor import _validate_tool_input

        rendered = _validate_tool_input(tool_name, tool_input)
        assert rendered is not None, "expected a violation"
        return rendered

    def test_names_the_offending_field(self):
        rendered = self._render("render_surface", {"kind": "message", "sections": []})
        assert "title" in rendered
        assert "render_surface" in rendered

    def test_is_capped_when_pydantic_reports_many_errors(self):
        """A broadly-malformed payload produces more errors than an agent can use.

        Unbounded, a pydantic v2 error dump is hundreds of characters per entry. The
        cap keeps the message short, still names the first offending field, and says
        how many were dropped.
        """
        from pydantic import ValidationError

        from src.orchestrator.tool_executor import _MAX_ARG_ERROR_CHARS
        from src.tools.schemas import RenderSurfaceInput

        bad = {
            "kind": "not_a_kind",
            "sections": [
                {"type": "Text"},
                {"id": "", "type": "Markdown"},
                {"id": "c3", "type": "Table", "properties": {}},
            ],
        }
        with pytest.raises(ValidationError) as exc_info:
            RenderSurfaceInput.model_validate(bad)
        raw_error_count = len(exc_info.value.errors())
        assert raw_error_count > 3, f"expected >3 raw errors, got {raw_error_count}"

        rendered = self._render("render_surface", bad)
        assert len(rendered) <= _MAX_ARG_ERROR_CHARS, f"{len(rendered)} chars: {rendered}"
        assert "more)" in rendered, "truncation must be visible, not silent"

    def test_tells_the_agent_what_to_do(self):
        """House style, set by the existing missing-required-args return: say what is
        wrong AND what to do."""
        rendered = self._render("render_surface", {"kind": "message", "sections": []})
        assert "again" in rendered.lower()

    def test_returns_none_for_a_valid_payload(self):
        from src.orchestrator.tool_executor import _validate_tool_input

        assert _validate_tool_input("render_surface", dict(_VALID_RENDER_SURFACE)) is None

    def test_returns_none_for_a_tool_with_no_model(self):
        from src.orchestrator.tool_executor import _validate_tool_input

        assert _validate_tool_input("search_gmail_messages", {"whatever": 1}) is None


def test_reject_flag_is_off():
    """R1 ships in survey mode. Flipping this is R2, gated on the survey's findings."""
    from src.orchestrator.tool_executor import _REJECT_ON_INVALID_TOOL_ARGS

    assert _REJECT_ON_INVALID_TOOL_ARGS is False


@pytest.mark.asyncio
async def test_a_validator_that_raises_does_not_break_dispatch(caplog):
    """Fail OPEN when validation itself explodes.

    Pydantic wraps ValueError/AssertionError into ValidationError, but not, say, a
    TypeError from a field_validator. That would escape ``execute_tool`` ABOVE its own
    try/except and kill a dispatch that worked yesterday. A survey may not break the
    thing it surveys — and in R2 a broken validator must not become a blocked tool.
    """
    caplog.set_level(logging.WARNING)

    from pydantic import BaseModel, field_validator

    class _ExplodingInput(BaseModel):
        """A tool whose validator raises something pydantic does not wrap."""

        anything: int = 0

        @field_validator("anything")
        @classmethod
        def _boom(cls, v: int) -> int:
            raise TypeError("validator blew up")

    with patch.dict(
        "src.tools.schemas.TOOL_INPUT_MODELS", {"render_surface": _ExplodingInput}, clear=False
    ):
        result, call_internal = await _run_internal("render_surface", {"anything": 1})

    call_internal.assert_awaited_once()
    assert result == {"status": "ok"}
    assert not [r for r in _toolargs_records(caplog) if "would be rejected" in r.getMessage()]
