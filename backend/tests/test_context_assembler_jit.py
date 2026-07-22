"""Step 8 P2 review nit: teeth test for the per-agent JIT gate in
``ContextAssembler.assemble_context`` (``context_assembler.py:237``).

``test_jit_enabled_agents_excludes_presenter_and_executor`` (in
``test_context_jit_wiring.py``) only asserts the ``JIT_ENABLED_AGENTS`` constant
set — it never calls ``assemble_context``, so mutating line 237 from
``use_jit = jit and agent_name in JIT_ENABLED_AGENTS`` to ``use_jit = jit`` would
pass every existing test while silently handing Presenter/Executor the slim pack.

These tests drive the real ``assemble_context`` call and assert the ``jit`` kwarg
actually passed to ``ContextBuilder.build`` — they fail under that mutation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.context_assembler import ContextAssembler
from src.orchestrator.services import ServiceContainer
from src.services.context_builder import ContextPack
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_assembler() -> ContextAssembler:
    """Build a real ContextAssembler with minimal mocks.

    ``db_factory_provider()`` must return a callable that, when invoked, yields
    an async context manager. A single AsyncMock session (its own
    ``__aenter__``/``__aexit__``) satisfies both the integration-context lookup
    and the ContextBuilder construction inside ``assemble_context`` — the
    integration-context query fails closed (caught exception -> "") since the
    session isn't wired with real SQLAlchemy result shapes, which is fine: this
    test only cares about the ``jit`` kwarg reaching ``ContextBuilder.build``.
    """
    db_session = MagicMock()
    db_session.__aenter__ = AsyncMock(return_value=db_session)
    db_session.__aexit__ = AsyncMock(return_value=False)
    # `.execute(...)` is the only DB call exercised (via _load_integration_context);
    # a plain MagicMock result with an empty `.scalars().all()` mirrors the real
    # SQLAlchemy Result shape (sync `.scalars()`/`.all()`) and avoids the
    # "coroutine never awaited" noise a pure AsyncMock chain would produce.
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db_session.execute = AsyncMock(return_value=result)

    def db_factory():
        return db_session

    services = ServiceContainer(world_model=MagicMock(), memory_service=MagicMock())

    return ContextAssembler(
        settings=make_mock_settings(),
        services=services,
        db_factory_provider=lambda: db_factory,
        client=MagicMock(),
    )


async def test_presenter_gated_to_eager_pack_even_when_jit_true():
    """Presenter is CONTEXT_ENRICHED but NOT in JIT_ENABLED_AGENTS -> eager (jit=False)
    regardless of the caller's jit=True. This is the case that a `use_jit = jit`
    mutation would flip silently."""
    assembler = _make_assembler()

    with patch("src.orchestrator.context_assembler.ContextBuilder") as mock_builder_cls:
        mock_instance = MagicMock()
        mock_instance.build = AsyncMock(return_value=ContextPack())
        mock_builder_cls.return_value = mock_instance
        mock_builder_cls.to_prompt = MagicMock(return_value="")

        await assembler.assemble_context(
            "presenter",
            "msg",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            jit=True,
        )

    mock_instance.build.assert_awaited_once()
    assert mock_instance.build.await_args.kwargs["jit"] is False


async def test_planner_gets_slim_pack_when_jit_true():
    """Companion: planner IS in JIT_ENABLED_AGENTS -> jit=True passes through."""
    assembler = _make_assembler()

    with patch("src.orchestrator.context_assembler.ContextBuilder") as mock_builder_cls:
        mock_instance = MagicMock()
        mock_instance.build = AsyncMock(return_value=ContextPack())
        mock_builder_cls.return_value = mock_instance
        mock_builder_cls.to_prompt = MagicMock(return_value="")

        await assembler.assemble_context(
            "planner",
            "msg",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            jit=True,
        )

    mock_instance.build.assert_awaited_once()
    assert mock_instance.build.await_args.kwargs["jit"] is True


def _make_db_session(execute_results: list) -> MagicMock:
    """A db session mock whose ``.execute(...)`` returns ``execute_results`` in
    call order (side_effect), each shaped like a real SQLAlchemy Result
    (sync ``.scalars().all()``)."""
    db_session = MagicMock()
    db_session.__aenter__ = AsyncMock(return_value=db_session)
    db_session.__aexit__ = AsyncMock(return_value=False)
    db_session.execute = AsyncMock(side_effect=execute_results)
    return db_session


def _empty_result() -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


async def test_forced_on_jit_agent_gets_real_slim_pack_end_to_end():
    """Task 4.1: FULL chain, forced on. A real ContextAssembler builds a real
    ContextBuilder (no mocking of ContextBuilder itself) for planner (a
    JIT_ENABLED_AGENTS member) with jit=True, and the resulting prompt string is
    the genuine SLIM render: compact entities + trailing retrieval hint, with the
    bulky eager-only sections absent.

    DB call order inside ``assemble_context``/``build(jit=True)``:
    1. ``_load_integration_context`` (own db.execute) -> empty
    2. ``_fetch_core_goals`` -> empty
    3. ``_fetch_core_entities`` -> one entity row ("Acme", org)
    """
    entity_row = MagicMock(entity_id="e1", canonical_name="Acme", entity_type="org")
    entities_result = MagicMock()
    entities_result.scalars.return_value.all.return_value = [entity_row]

    db_session = _make_db_session([_empty_result(), _empty_result(), entities_result])

    def db_factory():
        return db_session

    memory_service = MagicMock()
    memory_service.get_user_preferences = AsyncMock(
        return_value=[{"memory_id": "p1", "fact_text": "prefers concise"}]
    )

    services = ServiceContainer(world_model=MagicMock(), memory_service=memory_service)

    assembler = ContextAssembler(
        settings=make_mock_settings(),
        services=services,
        db_factory_provider=lambda: db_factory,
        client=MagicMock(),
    )

    result = await assembler.assemble_context(
        "planner",
        "what's next",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        jit=True,
    )

    # Slim markers: compact entity render + trailing retrieval hint.
    assert "Retrieving More Context" in result
    assert "get_entity" in result
    assert "Known Entities" in result
    assert "Acme (org)" in result
    # Eager-only decoration/sections must be absent from the slim render.
    assert "importance=" not in result
    assert "## Entity Relationships" not in result


async def test_forced_on_non_jit_agent_still_gets_real_eager_pack_end_to_end():
    """Companion to the above: executor is CONTEXT_ENRICHED but NOT in
    JIT_ENABLED_AGENTS, so even with the caller forcing jit=True, the real chain
    renders the EAGER pack — no retrieval hint. Proves Fork-3a's per-agent gate
    through the real render, not just the boolean passed to a mocked builder."""
    db_session = _make_db_session([_empty_result()] * 5)

    def db_factory():
        return db_session

    memory_service = MagicMock()
    memory_service.get_user_preferences = AsyncMock(
        return_value=[{"memory_id": "p1", "fact_text": "prefers concise"}]
    )

    services = ServiceContainer(world_model=MagicMock(), memory_service=memory_service)

    assembler = ContextAssembler(
        settings=make_mock_settings(),
        services=services,
        db_factory_provider=lambda: db_factory,
        client=MagicMock(),
    )

    result = await assembler.assemble_context(
        "executor",
        "do it",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        jit=True,
    )

    assert "Retrieving More Context" not in result
