"""Planning/governance-domain MCP tools: plans, policy, approvals, execution."""

import logging
from datetime import datetime, timezone

from fastmcp import Context
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations
from sqlalchemy import select

from src.integrations.mcp_errors import make_error_response
from src.models.approvals import Approval
from src.models.tool_definitions import ToolDefinition
from src.tools.intelligence_server import _shared
from src.tools.intelligence_server._shared import _get_db, intelligence

logger = logging.getLogger(__name__)


async def _get_plan_details_impl(
    plan_id: str,
    user_id: str,
    workspace_id: str,
    db,
) -> dict:
    """Core implementation for get_plan_details tool.

    Returns plan metadata or {"status": "not_found"} if plan doesn't exist
    or workspace doesn't match.
    """
    from src.models.plans import Plan

    result = await db.execute(
        select(Plan).where(
            Plan.plan_id == plan_id,
        )
    )
    plan = result.scalar_one_or_none()

    if not plan:
        return {"status": "not_found"}

    # Workspace isolation check (skip if workspace_id not provided)
    if workspace_id and plan.workspace_id != workspace_id:
        return {"status": "not_found"}

    # Build tasks list
    tasks = [
        {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "description": getattr(task, "description", ""),
            "depends_on": task.depends_on or [],
        }
        for task in (plan.tasks or [])
    ]

    return {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "priority": plan.priority,
        "risk_level": plan.risk_level,
        "decision": plan.decision,
        "status": plan.status,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "tasks": tasks,
    }


@intelligence.tool(
    tags={"governor", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_plan_details(
    user_id: str,
    plan_id: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Fetch plan metadata to verify existence and inspect tasks.

    Returns plan metadata including tasks list, or not_found status.
    Used by Governor to verify plan existence before policy evaluation.
    """
    async with _get_db() as db:
        try:
            return await _get_plan_details_impl(plan_id, user_id, workspace_id, db)
        except Exception as e:
            logger.error("get_plan_details failed: %s", e, exc_info=True)
            return {"status": "not_found", "error": str(e)}


@intelligence.tool(
    tags={"planner", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_active_plans(
    user_id: str,
    ctx: Context,
    limit: int = 10,
    workspace_id: str = "",
) -> dict:
    """Get currently active plans (not completed/failed/cancelled)."""
    async with _get_db() as db:
        try:
            from src.models.plans import Plan

            result = await db.execute(
                select(Plan)
                .where(Plan.user_id == user_id)
                .where(Plan.workspace_id == workspace_id)
                .where(Plan.status.notin_(["completed", "failed", "cancelled"]))
                .order_by(Plan.created_at.desc())
                .limit(limit)
            )
            plans = result.scalars().all()
            return {
                "plans": [
                    {
                        "plan_id": p.plan_id,
                        "goal": p.goal,
                        "priority": p.priority,
                        "status": p.status,
                        "decision": p.decision,
                    }
                    for p in plans
                ],
                "count": len(plans),
            }
        except Exception as e:
            logger.error("get_active_plans failed: %s", e, exc_info=True)
            return {"plans": [], "count": 0, "error": str(e)}


@intelligence.tool(
    tags={"governor", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def evaluate_policy(
    user_id: str,
    plan_id: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Evaluate governance policy for a plan.

    Returns: auto_execute, approval_required, or blocked — with reasoning.
    """
    async with _get_db() as db:
        try:
            governor = _shared.request_services(db).governor
            result = await governor.evaluate_plan(plan_id, user_id, workspace_id=workspace_id)
            return result.model_dump()
        except Exception as e:
            logger.error("evaluate_policy failed: %s", e, exc_info=True)
            return make_error_response(e)


@intelligence.tool(
    tags={"governor", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)
async def approve_action(
    user_id: str,
    approval_id: str,
    decision: str,
    ctx: Context,
    reason: str = "",
    workspace_id: str = "",
) -> dict:
    """Approve or reject a pending action.

    decision: 'approved' or 'rejected'
    """
    async with _get_db() as db:
        try:
            result = await db.execute(
                select(Approval).where(
                    Approval.approval_id == approval_id,
                    Approval.workspace_id == workspace_id,
                )
            )
            approval = result.scalar_one_or_none()
            if not approval:
                return {"status": "not_found"}
            if approval.status != "pending":
                return {"status": "already_decided", "current_status": approval.status}

            approval.status = decision
            approval.decided_at = datetime.now(timezone.utc)
            approval.decision_reason = reason
            await db.commit()

            # Log to audit
            audit = _shared.request_services(db).audit
            if audit:
                await audit.log(
                    user_id=user_id,
                    action_type=f"approval_{decision}",
                    summary=f"Approval {approval_id} {decision}: {reason}",
                    approval_id=approval_id,
                    policy_decision=decision,
                )

            await ctx.info(f"Approval {approval_id} {decision}")
            return {"status": decision, "approval_id": approval_id}
        except Exception as e:
            logger.error("approve_action failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.tool(
    tags={"operator", "write"},
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True),
)
async def update_execution(
    execution_id: str,
    status: str,
    ctx: Context,
    user_id: str = "",
    result_summary: str = "",
    error_message: str = "",
    workspace_id: str = "",
) -> dict:
    """Update the status of an execution.

    status: running, completed, failed
    """
    async with _get_db() as db:
        try:
            from src.models.task_graph import TaskRun

            result = await db.execute(
                select(TaskRun).where(
                    TaskRun.run_id == execution_id,
                    TaskRun.workspace_id == workspace_id,
                )
            )
            run = result.scalar_one_or_none()
            if not run:
                return {"status": "not_found"}

            from src.services.execution_state import InvalidTransitionError, transition_run

            try:
                transition_run(run, status)
            except InvalidTransitionError as e:
                return make_error_response(e)
            if status == "completed":
                run.completed_at = datetime.now(timezone.utc)
            if error_message:
                run.error = {"message": error_message}

            await db.flush()
            await db.commit()
            return {
                "status": "updated",
                "run_id": execution_id,
                "new_status": status,
            }
        except Exception as e:
            logger.error("update_execution failed: %s", e, exc_info=True)
            await db.rollback()
            return make_error_response(e)


@intelligence.tool(
    tags={"operator", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def verify_run(
    run_id: str,
    ctx: Context,
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Verify a completed run against success conditions.

    Returns verdict (passed/failed/partial/skipped) and details.
    """
    async with _get_db() as db:
        try:
            from src.models.plans import Plan
            from src.models.task_graph import TaskRun
            from src.services.verifier import Verifier

            run_result = await db.execute(
                select(TaskRun).where(
                    TaskRun.run_id == run_id,
                    TaskRun.workspace_id == workspace_id,
                )
            )
            run = run_result.scalar_one_or_none()
            if not run:
                return {
                    "verdict": "skipped",
                    "details": "Run not found",
                }

            conditions = None
            if run.plan_id:
                plan_result = await db.execute(
                    select(Plan).where(
                        Plan.plan_id == run.plan_id,
                        Plan.workspace_id == workspace_id,
                    )
                )
                plan = plan_result.scalar_one_or_none()
                if plan:
                    conditions = getattr(plan, "success_conditions", None)

            verifier = Verifier(_shared._settings, db)
            result = await verifier.verify_run(run_id, conditions)
            return {
                "verdict": result.verdict.value,
                "score": result.score,
                "details": result.details,
                "checks_passed": result.checks_passed,
                "checks_failed": result.checks_failed,
            }
        except Exception as e:
            logger.error("verify_run failed: %s", e, exc_info=True)
            return {"verdict": "skipped", "error": str(e)}


@intelligence.resource("plans://{workspace_id}/active")
async def active_plans_resource(workspace_id: str) -> str:
    """Currently active plans."""
    import json

    async with _get_db() as db:
        from src.models.plans import Plan

        result = await db.execute(
            select(Plan)
            .where(
                Plan.workspace_id == workspace_id,
                Plan.status.notin_(["completed", "failed", "cancelled"]),
            )
            .order_by(Plan.created_at.desc())
            .limit(10)
        )
        plans = result.scalars().all()
        return json.dumps(
            [
                {
                    "plan_id": p.plan_id,
                    "goal": p.goal,
                    "priority": p.priority,
                    "status": p.status,
                }
                for p in plans
            ]
        )


@intelligence.tool(
    tags={"planner", "read"},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)
async def discover_capabilities(
    query: str,
    ctx: Context,
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Search available capabilities by query.

    Returns matching capabilities with descriptions, tools,
    risk levels, and connection status.
    """
    try:
        async with _get_db() as db:
            stmt = select(ToolDefinition).where(ToolDefinition.enabled.is_(True))
            result = await db.execute(stmt)
            all_tools = list(result.scalars().all())

        matches: list[dict] = []
        query_lower = query.lower()
        seen_capabilities: set[str] = set()

        for tool in all_tools:
            if not tool.capability:
                continue
            cap = tool.capability
            desc = tool.description or ""
            if query_lower not in cap.lower() and query_lower not in desc.lower():
                continue
            if cap in seen_capabilities:
                for m in matches:
                    if m["capability"] == cap:
                        m["tools"].append(tool.name)
                        break
                continue

            seen_capabilities.add(cap)
            matches.append(
                {
                    "capability": cap,
                    "tools": [tool.name],
                    "risk": tool.risk_level or "none",
                    "status": "connected",
                    "description": desc,
                }
            )

        return {"capabilities": matches}
    except Exception as e:
        logger.error("discover_capabilities failed: %s", e, exc_info=True)
        return make_error_response(e)
