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
