"""OutcomeLearner — learn from a finished run's outcome.

Extracted from ``GraphExecutor`` (god-object decomposition, 2026-06-20). Owns
everything that happens *after* the DAG completes:

- ``writeback_memories`` — store execution memories and (unless every step was
  knowledge-routed) enrich the world model with entities/graph edges from the
  outcome, mirroring the chat path's InteractionLearner.
- ``run_verification`` — verify the completed run against the plan's success
  conditions and stamp the verdict on the checkpoint. ADVISORY: the verdict is
  recorded for learning/visibility but never terminal-fails a completed run.

It depends downward on ``StepGraphStore`` (read sibling outputs + checkpoint); the
verifier and db_factory are resolved via providers so the coordinator stays the
single source of truth (tests reassign ``_verifier`` / ``_db_factory`` after
construction). Background spawning stays coordinator-owned and is injected as a
callable. It never imports ``graph_executor``. ``transition_run`` (never direct
mutation) drives run-status changes.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from src.config.settings import Settings
from src.models.plans import Plan
from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_state import TERMINAL_SUCCESS, transition_run
from src.services.step_graph_store import StepGraphStore

logger = logging.getLogger(__name__)


class OutcomeLearner:
    """Post-run learning: memory writeback, entity sync, and verification."""

    def __init__(
        self,
        *,
        settings: Settings,
        db,
        store: StepGraphStore,
        spawn_background,
        db_factory_provider,
        verifier_provider,
        memory_service=None,
        world_model=None,
    ):
        self._settings = settings
        self._db = db
        self._store = store
        self._spawn_background = spawn_background
        self._db_factory_provider = db_factory_provider
        self._verifier_provider = verifier_provider
        self._memory_service = memory_service
        self._world_model = world_model

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    @property
    def _verifier(self):
        """Resolve the current verifier live via the provider."""
        return self._verifier_provider()

    @property
    def verification_enabled(self) -> bool:
        """Whether a verifier is wired (drives the partially_completed branch)."""
        return bool(self._verifier_provider())

    async def writeback_memories(self, run: TaskRun) -> None:
        """Learn from a completed autonomous run: store memories AND extract
        entities + graph relationships from the outcome.

        The entity/graph step brings the autonomous path to parity with the
        chat path's InteractionLearner (which only ran chat-side); without it,
        autonomous runs never enriched the world model from their own results.
        """
        if not self._memory_service:
            return
        all_steps = await self._store.get_all_steps(run.run_id)
        completed = [s for s in all_steps if s.status in TERMINAL_SUCCESS and s.output_data]
        if not completed:
            return
        parts = [f"Completed plan: {run.plan_id}"]
        for step in completed[:5]:
            parts.append(f"- {step.task_id}: {json.dumps(step.output_data)}")
        source_text = "\n".join(parts)

        try:
            await self._memory_service.extract_and_store(
                user_id=run.user_id,
                source_text=source_text,
                source_event_ids=[run.run_id],
                workspace_id=run.workspace_id,
            )
        except Exception:
            logger.warning(
                "Memory writeback failed for run %s — execution memories not stored",
                run.run_id,
                exc_info=True,
            )

        # Independent of memory storage: enrich the world model from the outcome,
        # unless every completed step was knowledge-routed — the Librarian already
        # extracted those entities during execution, so re-extracting from the
        # outcome would only repeat its work (idempotent, but a wasted LLM call).
        if not self._completed_all_knowledge_routed(completed):
            if self._db_factory is not None:
                # Run on its own session so the run's DB connection isn't held
                # open during the extraction LLM call (mirrors InteractionLearner).
                self._spawn_background(
                    self.learn_entities_isolated(
                        source_text, run.user_id, run.workspace_id or "", run.run_id
                    )
                )
            else:
                # No session factory wired (unit tests / minimal setups): fall
                # back to the inline path using the injected world_model.
                await self.learn_entities_from_outcome(source_text, run)

    @staticmethod
    def _completed_all_knowledge_routed(steps: list[TaskStep]) -> bool:
        """True when every completed step's capability routes to the Librarian
        (``knowledge.*``).

        Mixed plans return False and still extract: non-knowledge outputs (an
        email sent, an event created) carry entities the Librarian never saw. The
        ``isinstance`` guard keeps this safe for steps whose ``input_data`` is
        unset/non-dict — those are treated as non-knowledge so extraction runs."""
        caps = [
            s.input_data.get("capability") if isinstance(s.input_data, dict) else None
            for s in steps
        ]
        return bool(caps) and all(isinstance(c, str) and c.startswith("knowledge.") for c in caps)

    async def learn_entities_from_outcome(self, source_text: str, run: TaskRun) -> None:
        """Inline entity learning on the run's session via the injected world_model.

        Fallback for setups with no ``db_factory`` (unit tests); production
        backgrounds this via ``learn_entities_isolated`` so the run's connection
        isn't held during the extraction LLM call. Skipped when no world model
        is wired (e.g. unit tests inject ``world_model=None``)."""
        if not self._world_model:
            return
        await self.extract_and_sync_entities(
            self._db,
            self._world_model,
            source_text,
            run.user_id,
            run.workspace_id or "",
            run.run_id,
        )

    async def learn_entities_isolated(
        self, source_text: str, user_id: str, workspace_id: str, run_id: str
    ) -> None:
        """Entity learning on its own session + world_model so the run's DB
        connection isn't held during the extraction LLM call. Best-effort —
        commits independently and never affects the run (the run is already
        committed by the time this runs)."""
        if not self._db_factory:
            return
        try:
            from src.services.world_model import WorldModel

            async with self._db_factory() as db:
                world_model = WorldModel(self._settings, db)
                await self.extract_and_sync_entities(
                    db, world_model, source_text, user_id, workspace_id, run_id
                )
                await db.commit()
        except Exception:
            logger.debug("Isolated entity learning failed for run %s", run_id, exc_info=True)

    async def extract_and_sync_entities(
        self,
        db,
        world_model,
        source_text: str,
        user_id: str,
        workspace_id: str,
        run_id: str,
    ) -> None:
        """Shared core: extract entities from outcome text and sync them to the
        graph. Best-effort — entity/graph learning must never fail a run."""
        try:
            entity_ids = await world_model.extract_from_text(
                source_text,
                user_id=user_id,
                workspace_id=workspace_id,
            )
            if not entity_ids:
                return
            if not getattr(self._settings, "neo4j_url", None):
                return
            from src.services.graph_sync import GraphSyncService

            graph_sync = GraphSyncService(self._settings, db)
            try:
                await graph_sync.batch_sync_entities(entity_ids, workspace_id=workspace_id)
            finally:
                await graph_sync.close()
        except Exception:
            logger.debug("Entity learning from outcome failed for run %s", run_id, exc_info=True)

    async def run_verification(self, run: TaskRun) -> None:
        """Run verification on a completed run."""
        try:
            # Load success conditions from the plan
            plan_result = await self._db.execute(select(Plan).where(Plan.plan_id == run.plan_id))
            plan = plan_result.scalar_one_or_none()
            conditions = plan.success_conditions if plan else None

            result = await self._verifier.verify_run(run.run_id, conditions)
            # Store verdict in checkpoint
            await self._store.checkpoint(run, None, "verification")
            run.checkpoint = {
                **(run.checkpoint or {}),
                "verification": {
                    "verdict": result.verdict.value,
                    "score": result.score,
                    "details": result.details,
                },
            }
            # Verification is ADVISORY: the verdict is recorded above for
            # learning/visibility but never terminal-fails a run. Final status
            # follows STEP outcomes — a genuinely failed step is handled in
            # dag_runner (the blocked/failed branch), not here. This prevents a
            # soft/ harsh judge verdict from flipping a fully-completed run to
            # failed (which previously happened to 100% of verified runs) and
            # from falsely demoting trust on auto-executed capabilities.
            steps_result = await self._db.execute(
                select(TaskStep).where(TaskStep.run_id == run.run_id)
            )
            any_failed_step = any(
                s.status in ("failed", "timed_out") for s in steps_result.scalars().all()
            )
            if run.status == "partially_completed" and not any_failed_step:
                transition_run(run, "completed")
            if result.verdict.value == "failed":
                logger.info(
                    "Run %s advisory verification verdict (informational, not failing): %s",
                    run.run_id,
                    result.details,
                )
        except Exception:
            logger.warning("Verification failed for run %s", run.run_id, exc_info=True)
