"""Verifier — validates run outcomes against success conditions."""

import json
import logging
from enum import Enum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.llm.utility import complete_text
from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_state import TERMINAL_SUCCESS

logger = logging.getLogger(__name__)


class Verdict(str, Enum):
    passed = "passed"
    failed = "failed"
    partial = "partial"
    skipped = "skipped"


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verdict: Verdict
    score: float = 0.0
    details: str = ""
    checks_passed: list[str] = []
    checks_failed: list[str] = []


class Verifier:
    """Verify run outcomes against success conditions."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db

    async def verify_run(
        self,
        run_id: str,
        success_conditions: dict | None = None,
    ) -> VerificationResult:
        """Verify a completed run against its success conditions."""
        result = await self._db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            return VerificationResult(verdict=Verdict.skipped, details=f"Run not found: {run_id}")

        if not success_conditions:
            return VerificationResult(
                verdict=Verdict.skipped, details="No success conditions defined"
            )

        steps_result = await self._db.execute(select(TaskStep).where(TaskStep.run_id == run_id))
        steps = list(steps_result.scalars().all())

        checks_passed = []
        checks_failed = []

        conditions = success_conditions.get("conditions", [])
        if not conditions:
            conditions = [success_conditions]

        for condition in conditions:
            cond_type = self._resolve_cond_type(condition)
            passed = await self._check_condition(condition, cond_type, run, steps)
            label = condition.get("label", cond_type)
            if passed:
                checks_passed.append(label)
            else:
                checks_failed.append(label)

        total = len(checks_passed) + len(checks_failed)
        score = len(checks_passed) / total if total > 0 else 0.0

        if not checks_failed:
            verdict = Verdict.passed
        elif not checks_passed:
            verdict = Verdict.failed
        else:
            verdict = Verdict.partial

        return VerificationResult(
            verdict=verdict,
            score=score,
            details=f"{len(checks_passed)}/{total} checks passed",
            checks_passed=checks_passed,
            checks_failed=checks_failed,
        )

    @staticmethod
    def _resolve_cond_type(condition: dict) -> str:
        """Resolve a condition's check type.

        An explicit ``type`` always wins. Otherwise, an untyped condition that
        carries a free-text ``criteria`` key (how ``plan_store`` stores the
        Planner's prose ``success_criteria``) is routed to the LLM judge so the
        prose is actually evaluated — without this it falls through to
        ``status_equals``, which never reads the prose and produces a meaningless
        verdict. Untyped conditions with no ``criteria`` keep the historical
        ``status_equals`` default.
        """
        explicit = condition.get("type")
        if explicit:
            return explicit
        if "criteria" in condition:
            return "llm_judge"
        return "status_equals"

    async def verify_step(
        self,
        step_id: str,
        expected_output: dict | None = None,
    ) -> VerificationResult:
        """Verify a single step's output."""
        result = await self._db.execute(select(TaskStep).where(TaskStep.step_id == step_id))
        step = result.scalar_one_or_none()
        if not step:
            return VerificationResult(verdict=Verdict.skipped, details=f"Step not found: {step_id}")

        if step.status != "completed":
            return VerificationResult(
                verdict=Verdict.failed,
                details=f"Step not completed (status={step.status})",
                checks_failed=["step_completed"],
            )

        if not expected_output:
            return VerificationResult(
                verdict=Verdict.passed,
                score=1.0,
                details="Step completed, no output validation required",
                checks_passed=["step_completed"],
            )

        passed = []
        failed = []
        output = step.output_data or {}

        if "output_contains" in expected_output:
            needle = expected_output["output_contains"]
            if needle in json.dumps(output):
                passed.append("output_contains")
            else:
                failed.append("output_contains")

        if "output_matches_schema" in expected_output:
            required_keys = expected_output["output_matches_schema"]
            if all(k in output for k in required_keys):
                passed.append("output_matches_schema")
            else:
                failed.append("output_matches_schema")

        if "status_equals" in expected_output:
            if output.get("status") == expected_output["status_equals"]:
                passed.append("status_equals")
            else:
                failed.append("status_equals")

        total = len(passed) + len(failed)
        score = len(passed) / total if total > 0 else 1.0
        verdict = Verdict.passed if not failed else (Verdict.partial if passed else Verdict.failed)

        return VerificationResult(
            verdict=verdict,
            score=score,
            details=f"{len(passed)}/{total} checks passed",
            checks_passed=passed,
            checks_failed=failed,
        )

    async def _check_condition(
        self,
        condition: dict,
        cond_type: str,
        run: TaskRun,
        steps: list[TaskStep],
    ) -> bool:
        if cond_type == "status_equals":
            expected = condition.get("value")
            if expected is None:
                # No explicit target: accept the post-completion states. dag_runner
                # sets the run to ``partially_completed`` *before* verification, so
                # a default hardcoded to ``completed`` would always fail here.
                return run.status in ("completed", "partially_completed")
            return run.status == expected

        if cond_type == "all_steps_completed":
            return all(s.status in TERMINAL_SUCCESS for s in steps)

        if cond_type == "output_contains":
            needle = condition.get("value", "")
            for step in steps:
                if step.output_data and needle in json.dumps(step.output_data):
                    return True
            return False

        if cond_type == "artifact_created":
            for step in steps:
                refs = step.artifact_refs or []
                if refs:
                    return True
            return False

        if cond_type == "llm_judge":
            return await self._llm_judge(condition, run, steps)

        return True

    async def _llm_judge(
        self,
        condition: dict,
        run: TaskRun,
        steps: list[TaskStep],
    ) -> bool:
        """Use Claude to judge if the run met a qualitative condition."""
        criteria = condition.get("criteria", "Did the run complete successfully?")
        step_summaries = []
        for s in steps:
            out = json.dumps(s.output_data)[:200] if s.output_data else "no output"
            step_summaries.append(f"Step {s.task_id} ({s.status}): {out}")

        prompt = (
            f"Evaluation criteria: {criteria}\n\n"
            f"Run status: {run.status}\n"
            f"Steps:\n" + "\n".join(step_summaries) + "\n\n"
            'Respond with JSON: {"passed": true/false, "reason": "..."}'
        )

        try:
            # No assistant-message prefill: Muldro's adaptive-thinking models reject a
            # conversation ending in an assistant turn (400). Instead we instruct JSON-only
            # in the system prompt and rely on parse_llm_json to tolerate any stray prose.
            text = await complete_text(
                system=(
                    "You are a quality verification engine. "
                    "Evaluate whether the run met the criteria. "
                    'Respond with ONLY a JSON object: {"passed": true/false, "reason": "..."}'
                ),
                user=prompt,
                tier="resolved",
                max_tokens=256,
                workspace_id=run.workspace_id,
            )
            from src.llm_utils import parse_llm_json

            # Advisory verification: a malformed/empty judge response must NOT
            # raise (it is informational, not failing). Degrade to not-passed.
            result = parse_llm_json(text, default={"passed": False, "reason": "unparseable"})
            if result.get("reason") == "unparseable":
                logger.info("LLM judge returned no parseable JSON — treating as not-passed")
            return bool(result.get("passed", False))
        except Exception:
            logger.warning("LLM judge verification call failed", exc_info=True)
            return False
