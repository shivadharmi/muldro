"""ToolExecutor enforces TOOL_INPUT_MODELS at dispatch time: invalid args are rejected.

``TOOL_INPUT_MODELS`` (src/tools/schemas.py) was consulted at startup by
``validate_registry()`` and nowhere else at call time, so an internal tool's typed
schema never actually constrained a call. FastMCP checks only the coarse JSON type
of each argument, so every bound the Pydantic model declares — a Literal's admissible
values, a numeric floor, a length cap — passed straight through to the tool.

The parse now REJECTS: a call whose arguments fail their model never reaches the
tool, and the agent gets back an ``invalid_tool_args`` error naming the offending
field, which it can act on. These tests pin that, plus the two properties that make
enforcement safe:

* the parse runs on the AGENT-supplied input, before ``_enrich_internal_input``
  injects user_id/workspace_id — those context args are deliberately absent from the
  LLM-facing models, so validating after injection would reject every internal call;
* a validator that raises something pydantic does not wrap still fails OPEN — under
  enforcement a broken validator would otherwise turn into a permanently blocked tool.

``TestSizeAnnotation`` covers the second half of the bargain: a rejection is only useful
if the model can repair it in one attempt, which means telling it how far over a limit
it was — and never, under any circumstances, echoing the offending value back.
"""

import logging
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from pydantic import BaseModel, Field

from src.models.tool_definitions import ToolDefinition
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID

_TOOLARGS_PREFIX = "[toolargs]"

# A valid store_memory payload, exactly as an agent would emit it: no user_id,
# no workspace_id — the dispatcher injects those AFTER validation.
_VALID_STORE_MEMORY = {
    "text": "Revenue is up.",
    "memory_type": "fact",
    "scope": "general",
}

# The bound-annotation tests need a length-bounded field and a length-bounded list.
# No shipped input model declares either today, so they drive a synthetic model
# through the real renderer instead. That is not a weaker test: the annotation keys
# off the pydantic ERROR TYPE, never off the fields any one model happens to have,
# so which model produced the error is exactly the thing it must be indifferent to.
_BOUNDED_TOOL = "bounded_tool"


class _BoundedInput(BaseModel):
    """Stand-in for any tool whose schema declares length bounds."""

    subtitle: str = Field(default="", max_length=120)
    metrics: list[str] = Field(default_factory=list, max_length=4)


@contextmanager
def _bounded_model(model: type[BaseModel] = _BoundedInput):
    """Register ``model`` under ``_BOUNDED_TOOL`` for the duration of the block."""
    with patch.dict("src.tools.schemas.TOOL_INPUT_MODELS", {_BOUNDED_TOOL: model}, clear=False):
        yield


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
async def test_invalid_args_are_rejected_before_dispatch(caplog):
    """A violation is enforced, not merely reported.

    ``ttl_days`` is declared ``ge=0``. JSON-schema-level checking sees a valid integer
    and would let it through, so a bound that lives only in the Pydantic model is the
    honest test of whether the model is consulted at all.
    """
    caplog.set_level(logging.WARNING)
    bad_input = {**_VALID_STORE_MEMORY, "ttl_days": -1}

    result, call_internal = await _run_internal("store_memory", bad_input)

    logged = _toolargs_records(caplog)
    assert len(logged) == 1, f"expected exactly one [toolargs] warning, got {logged}"
    assert "store_memory" in logged[0].getMessage()

    call_internal.assert_not_awaited()
    assert result["error_code"] == "invalid_tool_args"
    assert "ttl_days" in result["error"]


@pytest.mark.asyncio
async def test_missing_required_field_is_rejected_before_dispatch(caplog):
    """A structurally incomplete call (no ``text``) is blocked, and the error says so.

    This is the end-to-end half of ``TestRenderedError.test_names_the_offending_field``:
    the field name has to survive all the way out of ``execute_tool``, not just out of
    ``_render_validation_error``.
    """
    caplog.set_level(logging.WARNING)
    bad_input = {"memory_type": "fact"}

    result, call_internal = await _run_internal("store_memory", bad_input)

    assert len(_toolargs_records(caplog)) == 1
    call_internal.assert_not_awaited()
    assert result["error_code"] == "invalid_tool_args"
    assert "text" in result["error"]
    assert "store_memory" in result["error"]


@pytest.mark.asyncio
async def test_valid_call_is_dispatched_untouched(caplog):
    """The other side of enforcement: a conforming payload is not disturbed."""
    caplog.set_level(logging.WARNING)

    result, call_internal = await _run_internal("store_memory", dict(_VALID_STORE_MEMORY))

    assert _toolargs_records(caplog) == []
    call_internal.assert_awaited_once()
    assert result == {"status": "ok"}
    assert "error" not in result
    # The agent-supplied args reach the tool untouched apart from context injection.
    assert call_internal.await_args.args[1]["text"] == _VALID_STORE_MEMORY["text"]


@pytest.mark.asyncio
async def test_context_args_are_not_required_by_the_input_model(caplog):
    """A valid agent payload WITHOUT the injected context args must validate cleanly.

    ``_VALID_STORE_MEMORY`` carries no user_id/workspace_id, because the dispatcher
    injects them after the parse. If a context field were ever added to an input model
    as required, this fails — and every internal tool call would log a false violation.
    """
    caplog.set_level(logging.WARNING)
    assert "user_id" not in _VALID_STORE_MEMORY
    assert "workspace_id" not in _VALID_STORE_MEMORY

    _, call_internal = await _run_internal("store_memory", dict(_VALID_STORE_MEMORY))

    assert _toolargs_records(caplog) == []
    # The context args ARE present by the time the tool is called. Injection is
    # signature-aware, and store_memory's impl declares both.
    assert call_internal.await_args.args[1]["user_id"] == TEST_USER_ID
    assert call_internal.await_args.args[1]["workspace_id"] == TEST_WORKSPACE_ID


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
        await _run_internal("store_memory", dict(_VALID_STORE_MEMORY))

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
    assert result["error_code"] == "invalid_tool_args", (
        "the passthrough must return the error, not echo an invalid verdict"
    )
    assert result != bad_input


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
    """The rendered string is what ``execute_tool`` RETURNS to the agent."""

    def _render(self, tool_name: str, tool_input: dict) -> str:
        from src.orchestrator.tool_executor import _validate_tool_input

        rendered = _validate_tool_input(tool_name, tool_input)
        assert rendered is not None, "expected a violation"
        return rendered

    def test_names_the_offending_field(self):
        rendered = self._render("store_memory", {"memory_type": "fact"})
        assert "text" in rendered
        assert "store_memory" in rendered

    def test_is_capped_when_pydantic_reports_many_errors(self):
        """A broadly-malformed payload produces more errors than an agent can use.

        Unbounded, a pydantic v2 error dump is hundreds of characters per entry. The
        cap keeps the message short, still names the first offending field, and says
        how many were dropped.
        """
        from pydantic import ValidationError

        from src.orchestrator.tool_executor import _MAX_ARG_ERROR_CHARS
        from src.tools.schemas import StoreMemoryInput

        bad = {
            "text": [],
            "memory_type": [],
            "scope": [],
            "ttl_days": -5,
            "entity_ids": [],
            "source": [],
        }
        with pytest.raises(ValidationError) as exc_info:
            StoreMemoryInput.model_validate(bad)
        raw_error_count = len(exc_info.value.errors())
        assert raw_error_count > 3, f"expected >3 raw errors, got {raw_error_count}"

        rendered = self._render("store_memory", bad)
        assert len(rendered) <= _MAX_ARG_ERROR_CHARS, f"{len(rendered)} chars: {rendered}"
        assert "more)" in rendered, "truncation must be visible, not silent"

    def test_tells_the_agent_what_to_do(self):
        """House style, set by the existing missing-required-args return: say what is
        wrong AND what to do."""
        rendered = self._render("store_memory", {"memory_type": "fact"})
        assert "again" in rendered.lower()

    def test_returns_none_for_a_valid_payload(self):
        from src.orchestrator.tool_executor import _validate_tool_input

        assert _validate_tool_input("store_memory", dict(_VALID_STORE_MEMORY)) is None

    def test_returns_none_for_a_tool_with_no_model(self):
        from src.orchestrator.tool_executor import _validate_tool_input

        assert _validate_tool_input("search_gmail_messages", {"whatever": 1}) is None


@pytest.mark.asyncio
async def test_a_validator_that_raises_does_not_break_dispatch(caplog):
    """Fail OPEN when validation itself explodes.

    Pydantic wraps ValueError/AssertionError into ValidationError, but not, say, a
    TypeError from a field_validator. That would escape ``execute_tool`` ABOVE its own
    try/except and kill the whole turn. This matters MORE under enforcement, not less:
    a validator that explodes must not silently become a permanently blocked tool.
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
        "src.tools.schemas.TOOL_INPUT_MODELS", {"store_memory": _ExplodingInput}, clear=False
    ):
        result, call_internal = await _run_internal("store_memory", {"anything": 1})

    call_internal.assert_awaited_once()
    assert result == {"status": "ok"}
    assert not [r for r in _toolargs_records(caplog) if "rejected" in r.getMessage()]


class TestSizeAnnotation:
    """A rejected call must say how far over the limit it was, not just the limit.

    Measured live on 2026-08-20 (gpt-5-mini, all tiers): a model repairing an overlong
    string against a 120-character cap went 123 -> 141 -> 128 -> 109 chars. Attempt two
    was WORSE than attempt one, and the call was abandoned one retry short of success.
    It was guessing, because "String should have at most 120 characters" does not tell
    it what it sent. Pydantic knows: ``err["input"]`` is the offending value.
    """

    def _render(self, tool_name: str, tool_input: dict) -> str:
        from src.orchestrator.tool_executor import _validate_tool_input

        rendered = _validate_tool_input(tool_name, tool_input)
        assert rendered is not None, "expected a violation"
        return rendered

    def test_an_overlong_string_is_told_its_own_length(self):
        """The exact live case: 141 characters against a 120-character limit."""
        subtitle = (
            "Draft: comprehensive history, communications, decisions, outstanding items, "
            "and recommended next steps. Missing private calendars and emails."
        )
        assert len(subtitle) == 141, "the reproduction must not be reflowed"

        with _bounded_model():
            rendered = self._render(_BOUNDED_TOOL, {"subtitle": subtitle})

        assert "120" in rendered, "the limit must still be stated"
        assert "141" in rendered, f"the agent was not told what it sent: {rendered}"

    def test_never_echoes_the_offending_value_only_its_size(self):
        """SECURITY. The measure travels; the content never does.

        The values reaching ``_render_validation_error`` are user content — email
        bodies, meeting notes, contact details — and the string it produces is BOTH
        returned into the model's context AND written to ``logger.warning``. Echoing the
        value to explain the violation would write user content into the logs. So this
        builds a violation out of a distinctive secret-shaped payload and demands that
        not one recognisable fragment of it survives, while its length does.
        """
        secret = "sk-live-9f3a" + "Qx7ZmNp4Kd" * 15  # 162 chars, unmistakable if leaked
        assert len(secret) == 162

        with _bounded_model():
            rendered = self._render(_BOUNDED_TOOL, {"subtitle": secret})

        assert "162" in rendered, "the size must be reported"
        assert secret not in rendered
        assert "sk-live" not in rendered, f"the value leaked into the message: {rendered}"
        assert "Qx7ZmNp4Kd" not in rendered
        # Not even a truncated prefix: any 8-char window of the secret is a leak.
        for i in range(len(secret) - 8):
            assert secret[i : i + 8] not in rendered, f"leaked window at {i}: {rendered}"

    def test_a_too_short_string_is_told_its_own_length_too(self):
        """The bound in the other direction is the same guessing problem, reversed.

        No shipped input model carries a ``min_length`` today, so this drives it through
        a synthetic one — the annotation must key off the pydantic error type, not off
        the fields any particular tool happens to declare.
        """

        class _MinLenInput(BaseModel):
            slug: str = Field(min_length=10)

        with _bounded_model(_MinLenInput):
            rendered = self._render(_BOUNDED_TOOL, {"slug": "abc"})

        assert "10" in rendered
        assert "(got 3)" in rendered, f"the agent was not told what it sent: {rendered}"

    def test_an_unmeasurable_input_renders_without_raising(self):
        """``input`` may be ANY object, and this path must never raise.

        ``_validate_tool_input`` catches non-ValidationError explosions, but a raise
        from the RENDERER happens inside its ``except ValidationError`` block, so it
        propagates out of ``execute_tool`` above its own try/except and kills the whole
        turn — a strictly worse outcome than the tool error it was trying to describe.
        Both shapes are covered: an object with no ``__len__``, and one whose ``__len__``
        raises (a lazily-hydrated ORM collection is the realistic version of the second).
        """
        from pydantic import ValidationError

        from src.orchestrator.tool_executor import _render_validation_error

        class _LenExplodes:
            def __len__(self):
                raise RuntimeError("detached from session")

        for bad_input in (12345, _LenExplodes()):
            exc = ValidationError.from_exception_data(
                "_BoundedInput",
                [
                    {
                        "type": "string_too_long",
                        "loc": ("subtitle",),
                        "input": bad_input,
                        "ctx": {"max_length": 120},
                    }
                ],
            )
            rendered = _render_validation_error(_BOUNDED_TOOL, exc)
            assert "subtitle" in rendered
            assert "120" in rendered
            assert "(got" not in rendered, f"invented a size for {bad_input!r}: {rendered}"

    def test_errors_that_are_not_about_size_are_not_annotated(self):
        """A size is noise wherever the message already says everything actionable.

        ``missing`` has no value to measure; ``literal_error`` already enumerates the
        admissible values; a ``*_type`` error is about the KIND of the value, for which
        its size explains nothing. Annotating any of them would spend the agent's
        attention without narrowing its next attempt.
        """
        cases = {
            "missing (no approval_id)": {"decision": "approved"},
            "literal_error (unknown decision)": {"approval_id": "apr_1", "decision": "maybe"},
            "string_type (approval_id is a number)": {"approval_id": 5, "decision": "approved"},
        }
        for label, bad in cases.items():
            rendered = self._render("approve_action", bad)
            assert "(got" not in rendered, f"{label} was annotated with a size: {rendered}"

    def test_a_too_long_list_is_not_annotated_because_pydantic_already_counts_it(self):
        """Collection bounds are excluded on evidence, not by oversight.

        ``too_long``/``too_short`` (list, dict, set) put the actual count in their OWN
        ``msg`` — "should have at most 4 items after validation, not 5". Annotating them
        would say the same number twice and read as two different facts.
        """
        five_metrics = [f"m{i}" for i in range(5)]

        with _bounded_model():
            rendered = self._render(_BOUNDED_TOOL, {"metrics": five_metrics})

        assert "not 5" in rendered, f"pydantic stopped reporting the count: {rendered}"
        assert "(got" not in rendered, f"the count was stated twice: {rendered}"
