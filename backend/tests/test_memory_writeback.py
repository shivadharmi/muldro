"""Tests for Phase 3A: Memory writeback from execution results."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_mock_settings


class _FakeSessionCtx:
    """Minimal async-context-manager standing in for a db_factory() session."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _make_executor(memory_service=None, world_model=None):
    """Create a GraphExecutor with mocked dependencies."""
    from src.services.graph_executor import GraphExecutor

    settings = make_mock_settings()
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()

    with patch("src.services.graph_executor.get_anthropic_client"):
        executor = GraphExecutor(
            settings=settings,
            db=db,
            memory_service=memory_service,
            world_model=world_model,
        )
    return executor


def _make_run(run_id="run_001", plan_id="plan_001", user_id="usr_1", status="completed"):
    run = MagicMock()
    run.run_id = run_id
    run.plan_id = plan_id
    run.user_id = user_id
    run.status = status
    return run


def _make_step(step_id, task_id, status="completed", output_data=None):
    step = MagicMock()
    step.step_id = step_id
    step.task_id = task_id
    step.status = status
    step.output_data = output_data
    return step


class TestMemoryWriteback:
    async def test_writeback_calls_extract_and_store(self):
        """Completed run with output data triggers memory extraction."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [
            _make_step("s1", "t1", "completed", {"result": "email sent"}),
            _make_step("s2", "t2", "completed", {"result": "calendar created"}),
        ]
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)

        mem_svc.extract_and_store.assert_called_once()
        call_kwargs = mem_svc.extract_and_store.call_args[1]
        assert call_kwargs["user_id"] == "usr_1"
        assert "plan_001" in call_kwargs["source_text"]
        assert "run_001" in call_kwargs["source_event_ids"]

    async def test_writeback_skipped_without_memory_service(self):
        """No memory_service means writeback is silently skipped."""
        executor = _make_executor(memory_service=None)
        run = _make_run()
        # Should not raise
        await executor._writeback_memories(run)

    async def test_writeback_skipped_no_completed_steps(self):
        """No completed steps with output → no writeback."""
        mem_svc = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [
            _make_step("s1", "t1", "failed", None),
            _make_step("s2", "t2", "skipped", None),
        ]
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)
        mem_svc.extract_and_store.assert_not_called()

    async def test_writeback_caps_at_5_steps(self):
        """Only first 5 completed steps are included in writeback text."""
        mem_svc = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [_make_step(f"s{i}", f"t{i}", "completed", {"r": f"result_{i}"}) for i in range(10)]
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)

        call_kwargs = mem_svc.extract_and_store.call_args[1]
        # Header + 5 step lines
        lines = call_kwargs["source_text"].split("\n")
        assert len(lines) == 6  # 1 header + 5 steps

    async def test_writeback_tolerates_extract_failure(self):
        """If extract_and_store raises, writeback doesn't propagate."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock(side_effect=RuntimeError("boom"))
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [_make_step("s1", "t1", "completed", {"ok": True})]
        executor._get_all_steps = AsyncMock(return_value=steps)

        # Should not raise
        await executor._writeback_memories(run)

    async def test_writeback_only_includes_steps_with_output(self):
        """Steps with None output_data are excluded."""
        mem_svc = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [
            _make_step("s1", "t1", "completed", {"data": "yes"}),
            _make_step("s2", "t2", "completed", None),  # no output
        ]
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)

        call_kwargs = mem_svc.extract_and_store.call_args[1]
        lines = call_kwargs["source_text"].split("\n")
        assert len(lines) == 2  # header + 1 step with output


class TestAutonomousLearningParity:
    """The autonomous path must learn entities + graph relationships from its
    outcomes, reaching parity with the chat path's InteractionLearner (which
    the autonomous path never invoked)."""

    async def test_writeback_extracts_entities_and_syncs_graph(self):
        """Completed run extracts entities from outcome text and syncs to graph."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock()
        world_model = AsyncMock()
        world_model.extract_from_text = AsyncMock(return_value=["ent_1", "ent_2"])
        executor = _make_executor(memory_service=mem_svc, world_model=world_model)

        run = _make_run()
        run.workspace_id = "ws_1"
        steps = [_make_step("s1", "t1", "completed", {"result": "met with Acme Corp"})]
        executor._get_all_steps = AsyncMock(return_value=steps)

        gs = AsyncMock()
        gs.batch_sync_entities = AsyncMock()
        gs.close = AsyncMock()
        with patch("src.services.graph_sync.GraphSyncService", return_value=gs):
            await executor._writeback_memories(run)

        world_model.extract_from_text.assert_awaited_once()
        _args, _kwargs = world_model.extract_from_text.call_args
        source_text = _args[0] if _args else _kwargs.get("text")
        assert "Acme Corp" in source_text
        assert _kwargs.get("user_id") == "usr_1"
        assert _kwargs.get("workspace_id") == "ws_1"
        gs.batch_sync_entities.assert_awaited_once_with(["ent_1", "ent_2"], workspace_id="ws_1")

    async def test_writeback_no_graph_sync_when_no_entities(self):
        """No entities extracted → no graph sync attempted."""
        mem_svc = AsyncMock()
        world_model = AsyncMock()
        world_model.extract_from_text = AsyncMock(return_value=[])
        executor = _make_executor(memory_service=mem_svc, world_model=world_model)

        run = _make_run()
        run.workspace_id = "ws_1"
        steps = [_make_step("s1", "t1", "completed", {"result": "nothing notable"})]
        executor._get_all_steps = AsyncMock(return_value=steps)

        gs = AsyncMock()
        gs.batch_sync_entities = AsyncMock()
        with patch("src.services.graph_sync.GraphSyncService", return_value=gs):
            await executor._writeback_memories(run)

        world_model.extract_from_text.assert_awaited_once()
        gs.batch_sync_entities.assert_not_awaited()

    async def test_writeback_no_entity_learning_without_world_model(self):
        """No world_model collaborator → entity learning silently skipped."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock()
        executor = _make_executor(memory_service=mem_svc, world_model=None)

        run = _make_run()
        steps = [_make_step("s1", "t1", "completed", {"result": "x"})]
        executor._get_all_steps = AsyncMock(return_value=steps)

        # Should not raise and should still store memories
        await executor._writeback_memories(run)
        mem_svc.extract_and_store.assert_called_once()

    async def test_writeback_tolerates_entity_learning_failure(self):
        """An entity-learning failure never breaks memory writeback."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock()
        world_model = AsyncMock()
        world_model.extract_from_text = AsyncMock(side_effect=RuntimeError("boom"))
        executor = _make_executor(memory_service=mem_svc, world_model=world_model)

        run = _make_run()
        run.workspace_id = "ws_1"
        steps = [_make_step("s1", "t1", "completed", {"result": "x"})]
        executor._get_all_steps = AsyncMock(return_value=steps)

        # Should not raise
        await executor._writeback_memories(run)
        mem_svc.extract_and_store.assert_called_once()

    async def test_writeback_skips_extraction_when_all_knowledge_routed(self):
        """Every completed step knowledge-routed → the Librarian already extracted
        those entities, so the redundant outcome pass is skipped (memory writeback
        still runs)."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock()
        world_model = AsyncMock()
        world_model.extract_from_text = AsyncMock(return_value=["ent_1"])
        executor = _make_executor(memory_service=mem_svc, world_model=world_model)

        run = _make_run()
        run.workspace_id = "ws_1"
        steps = [
            _make_step("s1", "t1", "completed", {"r": "x"}),
            _make_step("s2", "t2", "completed", {"r": "y"}),
        ]
        for s in steps:
            s.input_data = {"capability": "knowledge.search"}
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)

        world_model.extract_from_text.assert_not_awaited()
        mem_svc.extract_and_store.assert_awaited_once()

    async def test_writeback_extracts_when_mixed_routing(self):
        """A single non-knowledge step keeps the outcome entity-extraction alive —
        its output carries entities the Librarian never saw."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock()
        world_model = AsyncMock()
        world_model.extract_from_text = AsyncMock(return_value=["ent_1"])
        executor = _make_executor(memory_service=mem_svc, world_model=world_model)

        run = _make_run()
        run.workspace_id = "ws_1"
        s1 = _make_step("s1", "t1", "completed", {"r": "x"})
        s1.input_data = {"capability": "knowledge.search"}
        s2 = _make_step("s2", "t2", "completed", {"r": "y"})
        s2.input_data = {"capability": "email.send"}
        executor._get_all_steps = AsyncMock(return_value=[s1, s2])

        gs = AsyncMock()
        gs.batch_sync_entities = AsyncMock()
        gs.close = AsyncMock()
        with patch("src.services.graph_sync.GraphSyncService", return_value=gs):
            await executor._writeback_memories(run)

        world_model.extract_from_text.assert_awaited_once()


class TestEntityLearningIsolation:
    """Entity learning runs on its own session when a db_factory is wired, so the
    run's DB connection isn't held during the extraction LLM call."""

    async def test_learn_entities_isolated_uses_fresh_session_and_commits(self):
        executor = _make_executor()
        fresh_db = AsyncMock()
        fresh_db.commit = AsyncMock()
        executor._db_factory = lambda: _FakeSessionCtx(fresh_db)

        wm = AsyncMock()
        wm.extract_from_text = AsyncMock(return_value=["ent_1"])
        gs = AsyncMock()
        gs.batch_sync_entities = AsyncMock()
        gs.close = AsyncMock()
        with (
            patch("src.services.world_model.WorldModel", return_value=wm),
            patch("src.services.graph_sync.GraphSyncService", return_value=gs),
        ):
            await executor._learn_entities_isolated("text", "usr_1", "ws_1", "run_1")

        wm.extract_from_text.assert_awaited_once()
        gs.batch_sync_entities.assert_awaited_once_with(["ent_1"], workspace_id="ws_1")
        fresh_db.commit.assert_awaited_once()

    async def test_learn_entities_isolated_noop_without_factory(self):
        executor = _make_executor()
        executor._db_factory = None
        # Must not raise.
        await executor._learn_entities_isolated("text", "usr_1", "ws_1", "run_1")

    async def test_writeback_backgrounds_entity_learning_when_factory_present(self):
        """With a db_factory wired, writeback spawns a background task that learns
        on a fresh session/world_model — the injected world_model is NOT used inline
        (which would hold the run's connection during the LLM call)."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock()
        injected_wm = AsyncMock()
        injected_wm.extract_from_text = AsyncMock(return_value=["should_not_run"])
        executor = _make_executor(memory_service=mem_svc, world_model=injected_wm)

        fresh_db = AsyncMock()
        fresh_db.commit = AsyncMock()
        executor._db_factory = lambda: _FakeSessionCtx(fresh_db)

        run = _make_run()
        run.workspace_id = "ws_1"
        step = _make_step("s1", "t1", "completed", {"r": "met Acme Corp"})
        step.input_data = {"capability": "email.send"}  # non-knowledge → extraction runs
        executor._get_all_steps = AsyncMock(return_value=[step])

        fresh_wm = AsyncMock()
        fresh_wm.extract_from_text = AsyncMock(return_value=["ent_1"])
        gs = AsyncMock()
        gs.batch_sync_entities = AsyncMock()
        gs.close = AsyncMock()
        with (
            patch("src.services.world_model.WorldModel", return_value=fresh_wm),
            patch("src.services.graph_sync.GraphSyncService", return_value=gs),
        ):
            await executor._writeback_memories(run)
            assert executor._background_tasks  # a task was spawned
            await asyncio.gather(*list(executor._background_tasks))

        injected_wm.extract_from_text.assert_not_awaited()
        fresh_wm.extract_from_text.assert_awaited_once()
        fresh_db.commit.assert_awaited_once()


class TestGraphExecutorMemoryServiceParam:
    def test_memory_service_stored(self):
        """memory_service param is stored on the executor."""
        mem_svc = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)
        assert executor._memory_service is mem_svc

    def test_memory_service_defaults_none(self):
        executor = _make_executor()
        assert executor._memory_service is None
