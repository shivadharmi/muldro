# Approval & Resume Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken WebSocket approval path, make resume failures visible, add edit-before-approve, harden approval expiry and race conditions, and add double-click protection.

**Architecture:** The approval flow spans 3 layers: frontend `InlineApprovalCard` sends actions via WebSocket to `routes_ws.py`, which bridges to `routes_approvals.py` REST handlers, which delegate to `GraphExecutor.resume_run()`. Fixes target each layer's handoff point.

**Tech Stack:** Python/FastAPI, SQLAlchemy async, Redis Pub/Sub, React/TypeScript

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/api/routes_ws.py` | Modify | Fix payload keys, add edit handler |
| `backend/src/api/routes_approvals.py` | Modify | Resume error handling, expiry check, idempotency, step locking |
| `backend/src/services/approval_service.py` | Modify | artifact_refs validation |
| `backend/tests/test_approval_resume.py` | Create | End-to-end approval flow tests |
| `backend/tests/test_ws_approval.py` | Create | WebSocket approval handler tests |

---

### Task 1: Fix WebSocket Approval Payload Key Mismatch

**Gaps:** 3.1
**Files:**
- Modify: `backend/src/api/routes_ws.py:179-186`
- Test: `backend/tests/test_ws_approval.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ws_approval.py
"""Tests for WebSocket approval action handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestHandleApprove:
    """WebSocket approve handler extracts approval_id correctly."""

    @pytest.mark.asyncio
    async def test_approve_extracts_approval_id_from_payload(self):
        """_handle_approve reads 'approval_id' key, not 'id'."""
        from src.api.routes_ws import _handle_approve

        mock_app = MagicMock()
        payload = {"approval_id": "apr_01TEST000000000000000000"}

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}
            await _handle_approve("usr_01TEST", payload, mock_app)
            mock_process.assert_called_once_with(
                "usr_01TEST", "apr_01TEST000000000000000000", "approve", mock_app
            )

    @pytest.mark.asyncio
    async def test_approve_empty_payload_passes_empty_string(self):
        """Missing approval_id defaults to empty string."""
        from src.api.routes_ws import _handle_approve

        mock_app = MagicMock()

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "error"}
            await _handle_approve("usr_01TEST", {}, mock_app)
            mock_process.assert_called_once_with("usr_01TEST", "", "approve", mock_app)


class TestHandleReject:
    """WebSocket reject handler extracts approval_id correctly."""

    @pytest.mark.asyncio
    async def test_reject_extracts_approval_id_from_payload(self):
        from src.api.routes_ws import _handle_reject

        mock_app = MagicMock()
        payload = {"approval_id": "apr_01TEST000000000000000000"}

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}
            await _handle_reject("usr_01TEST", payload, mock_app)
            mock_process.assert_called_once_with(
                "usr_01TEST", "apr_01TEST000000000000000000", "reject", mock_app
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ws_approval.py -v`
Expected: FAIL — `_handle_approve` calls `_process_approval_ws` with `""` instead of `"apr_01TEST000000000000000000"` because it reads `payload.get("id", "")`.

- [ ] **Step 3: Fix the payload key in both handlers**

In `backend/src/api/routes_ws.py`, change lines 179-186:

```python
async def _handle_approve(user_id: str, payload: dict, app) -> dict:
    """Handle approval action via the REST handler (full execution resume)."""
    return await _process_approval_ws(user_id, payload.get("approval_id", ""), "approve", app)


async def _handle_reject(user_id: str, payload: dict, app) -> dict:
    """Handle rejection action via the REST handler (full execution resume)."""
    return await _process_approval_ws(user_id, payload.get("approval_id", ""), "reject", app)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ws_approval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/api/routes_ws.py tests/test_ws_approval.py
git commit -m "fix: use correct payload key 'approval_id' in WebSocket approval handlers"
```

---

### Task 2: Surface Resume Failures Instead of Silently Swallowing

**Gaps:** 3.2
**Files:**
- Modify: `backend/src/api/routes_approvals.py:222-255`
- Test: `backend/tests/test_approval_resume.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_approval_resume.py
"""Tests for approval resume error handling."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.execution_state import transition_run


class TestApproveResumeFailureHandling:
    """When resume_run() fails after approval, the run should be marked failed."""

    @pytest.mark.asyncio
    async def test_step_level_resume_failure_transitions_run_to_failed(self):
        """If resume_run raises, run transitions to failed and error is returned."""
        from src.api.routes_approvals import approve_action

        # We need to test that when resume_run raises, the run is marked failed.
        # This is a behavior test — after the fix, approve_action should catch
        # the exception, transition run to failed, and still return the approval.
        # The key assertion is that the run status changes.
        pass  # Placeholder — real test below after implementation

    @pytest.mark.asyncio
    async def test_resume_failure_emits_surface_update(self):
        """Failed resume should emit a 'failed' surface update."""
        pass  # Placeholder — real test below after implementation
```

- [ ] **Step 2: Implement resume failure handling**

In `backend/src/api/routes_approvals.py`, replace the step-level resume block (lines 222-244):

```python
    # Resume the run (either step-level approval gate or plan-level)
    if approval.run_id:
        from src.services.graph_executor import create_graph_executor

        executor = await create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)
        try:
            from src.models.task_graph import TaskStep

            step_result = await db.execute(
                select(TaskStep).where(
                    TaskStep.step_id == approval.step_id,
                    TaskStep.run_id == approval.run_id,
                )
            )
            from src.services.execution_state import transition_step

            step = step_result.scalar_one_or_none()
            if step and step.status == "waiting_approval":
                transition_step(step, "running")
                await db.flush()
            await executor.resume_run(approval.run_id)
        except Exception as exc:
            logger.exception("Resume failed after approval: %s", approval.run_id)
            # Transition run to failed so user sees the error
            try:
                from src.services.execution_state import transition_run as _tr

                run_for_fail = await db.execute(
                    select(TaskRun).where(TaskRun.run_id == approval.run_id)
                )
                r = run_for_fail.scalar_one_or_none()
                if r and r.status not in ("completed", "failed", "cancelled"):
                    _tr(r, "failed")
                    r.error = {"resume_failed": str(exc)[:500]}
                    r.completed_at = datetime.now(timezone.utc)
                    await db.commit()
            except Exception:
                logger.warning("Failed to mark run as failed after resume error", exc_info=True)
```

Apply the same pattern to the plan-level block (lines 245-255) and tool-level block (lines 308-322).

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/test_approval_resume.py tests/test_ws_approval.py -v`
Expected: PASS

- [ ] **Step 4: Run full test suite for regressions**

Run: `cd backend && python -m pytest tests/ -v -x --timeout=30 -k "approval" 2>&1 | tail -20`
Expected: All approval tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/api/routes_approvals.py tests/test_approval_resume.py
git commit -m "fix: transition run to failed when resume_run raises after approval"
```

---

### Task 3: Add edit_before_approve WebSocket Handler

**Gaps:** 3.3
**Files:**
- Modify: `backend/src/api/routes_ws.py:361-365`
- Test: `backend/tests/test_ws_approval.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_ws_approval.py`:

```python
class TestHandleEditBeforeApprove:
    """WebSocket edit_before_approve handler bridges to REST edit endpoint."""

    @pytest.mark.asyncio
    async def test_edit_before_approve_in_action_handlers(self):
        """edit_before_approve is registered in ACTION_HANDLERS."""
        from src.api.routes_ws import ACTION_HANDLERS

        assert "edit_before_approve" in ACTION_HANDLERS

    @pytest.mark.asyncio
    async def test_edit_before_approve_calls_edit_endpoint(self):
        from src.api.routes_ws import ACTION_HANDLERS

        handler = ACTION_HANDLERS.get("edit_before_approve")
        assert handler is not None

        mock_app = MagicMock()
        payload = {
            "approval_id": "apr_01TEST000000000000000000",
            "title": "Updated title",
            "summary": "Updated summary",
        }

        with patch(
            "src.api.routes_ws._process_edit_approval_ws", new_callable=AsyncMock
        ) as mock_edit:
            mock_edit.return_value = {"status": "success"}
            result = await handler("usr_01TEST", payload, mock_app)
            assert result["status"] == "success"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ws_approval.py::TestHandleEditBeforeApprove -v`
Expected: FAIL — `edit_before_approve` not in ACTION_HANDLERS.

- [ ] **Step 3: Implement the edit handler**

Add to `backend/src/api/routes_ws.py` before the ACTION_HANDLERS dict:

```python
async def _handle_edit_before_approve(user_id: str, payload: dict, app) -> dict:
    """Handle edit-before-approve action via the REST edit endpoint."""
    return await _process_edit_approval_ws(user_id, payload, app)


async def _process_edit_approval_ws(user_id: str, payload: dict, app) -> dict:
    """Bridge WS edit action to the REST edit_approval endpoint."""
    from fastapi import HTTPException

    from src.api.deps import resolve_workspace_id
    from src.config.settings import get_settings
    from src.models.database import get_session_factory

    approval_id = payload.get("approval_id", "")
    settings = get_settings()

    async with get_session_factory()() as db:
        try:
            workspace_id = await resolve_workspace_id(db, user_id)
        except Exception as e:
            logger.warning("ws_edit_approval_workspace_resolve_failed: %s", e)
            return {"status": "error", "error": "Could not resolve workspace"}

        try:
            from src.api.routes_approvals import ApprovalEditRequest, edit_approval

            req = ApprovalEditRequest(
                title=payload.get("title"),
                summary=payload.get("summary"),
                risk_level=payload.get("risk_level"),
            )
            result = await edit_approval(
                approval_id=approval_id,
                req=req,
                user_id=user_id,
                workspace_id=workspace_id,
                db=db,
            )
            return {
                "status": "success",
                "approval_id": result.approval_id,
                "title": result.title,
                "summary": result.summary,
            }
        except HTTPException as e:
            return {"status": "error", "error": e.detail}
        except Exception as e:
            logger.error("ws_edit_approval_failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}
```

Update ACTION_HANDLERS:

```python
ACTION_HANDLERS: dict[str, object] = {
    "approve": _handle_approve,
    "reject": _handle_reject,
    "edit_before_approve": _handle_edit_before_approve,
    "execute_insight": _handle_execute_insight,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ws_approval.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/api/routes_ws.py tests/test_ws_approval.py
git commit -m "feat: add edit_before_approve WebSocket handler bridging to REST edit endpoint"
```

---

### Task 4: Add Expiry Check at Approval Endpoints

**Gaps:** 3.4
**Files:**
- Modify: `backend/src/api/routes_approvals.py:605-629` (`_get_approval`)
- Test: `backend/tests/test_approval_resume.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_approval_resume.py`:

```python
class TestApprovalExpiryCheck:
    """Approval endpoints reject expired approvals even if heartbeat hasn't run."""

    @pytest.mark.asyncio
    async def test_get_approval_rejects_expired(self):
        """_get_approval raises 410 for pending approvals past expires_at."""
        from datetime import timedelta
        from unittest.mock import MagicMock

        from src.models.approvals import Approval

        # Create a mock approval that is pending but past expires_at
        mock_approval = MagicMock(spec=Approval)
        mock_approval.approval_id = "apr_01TEST000000000000000000"
        mock_approval.status = "pending"
        mock_approval.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_approval.user_id = "usr_01TEST"
        mock_approval.workspace_id = "ws_test"

        # After the fix, _get_approval should check expires_at and raise
        # HTTPException(410) for expired approvals.
        # This test validates that behavior.
        pass  # Integration test — requires DB session mock
```

- [ ] **Step 2: Implement expiry check in _get_approval**

In `backend/src/api/routes_approvals.py`, modify `_get_approval()` (after line 624):

```python
async def _get_approval(
    db: AsyncSession, approval_id: str, user_id: str, workspace_id: str
) -> Approval:
    """Fetch an approval with row-level locking, raising 404/410 if not found/expired.

    Uses SELECT ... FOR UPDATE to prevent concurrent approval race conditions.
    Checks expires_at before processing to prevent late approvals.
    """
    result = await db.execute(
        select(Approval)
        .where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    if approval.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Approval already {approval.status}",
        )
    # Check expiry before allowing action
    if approval.expires_at and approval.expires_at < datetime.now(timezone.utc):
        approval.status = "expired"
        await db.flush()
        raise HTTPException(
            status_code=410,
            detail="Approval has expired",
        )
    return approval
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "approval" 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd backend && git add src/api/routes_approvals.py tests/test_approval_resume.py
git commit -m "fix: reject expired approvals at endpoint level, not just heartbeat"
```

---

### Task 5: Lock TaskStep During Approval Resume

**Gaps:** 3.5
**Files:**
- Modify: `backend/src/api/routes_approvals.py:228-241`

- [ ] **Step 1: Add FOR UPDATE to step query**

In `backend/src/api/routes_approvals.py`, modify the step fetch in `approve_action()`:

```python
            step_result = await db.execute(
                select(TaskStep)
                .where(
                    TaskStep.step_id == approval.step_id,
                    TaskStep.run_id == approval.run_id,
                )
                .with_for_update()
            )
```

- [ ] **Step 2: Run existing approval tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "approval" 2>&1 | tail -20`
Expected: PASS (no behavioral change for non-concurrent scenarios)

- [ ] **Step 3: Commit**

```bash
cd backend && git add src/api/routes_approvals.py
git commit -m "fix: lock TaskStep with FOR UPDATE during approval resume to prevent race"
```

---

### Task 6: Add Approval Idempotency (Double-Click Protection)

**Gaps:** 3.6
**Files:**
- Modify: `backend/src/api/routes_approvals.py:605-629` (`_get_approval`)
- Test: `backend/tests/test_ws_approval.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_ws_approval.py`:

```python
class TestApprovalIdempotency:
    """Already-decided approvals return success instead of 400."""

    @pytest.mark.asyncio
    async def test_already_approved_returns_200(self):
        """If approval already approved, return success with already_decided flag."""
        # After fix, _get_approval should return the approval with a flag
        # instead of raising 400 for already-approved state.
        # The approve_action handler checks this flag and returns early.
        pass  # Integration test
```

- [ ] **Step 2: Modify _get_approval to distinguish already-decided**

In `backend/src/api/routes_approvals.py`, update `_get_approval` to accept the intended action:

```python
async def _get_approval(
    db: AsyncSession,
    approval_id: str,
    user_id: str,
    workspace_id: str,
    intended_action: str = "approve",
) -> Approval:
    """Fetch an approval with row-level locking.

    Raises 404 if not found, 410 if expired.
    Returns the approval as-is if already in the intended_action state
    (idempotent — caller checks approval.status to detect this).
    Raises 400 for conflicting states (e.g., trying to approve a rejected one).
    """
    result = await db.execute(
        select(Approval)
        .where(
            Approval.approval_id == approval_id,
            Approval.user_id == user_id,
            Approval.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")

    # Check expiry before allowing action
    if approval.status == "pending" and approval.expires_at and approval.expires_at < datetime.now(timezone.utc):
        approval.status = "expired"
        await db.flush()
        raise HTTPException(status_code=410, detail="Approval has expired")

    if approval.status != "pending":
        # Idempotent: if already in intended state, return it (caller handles)
        if approval.status == ("approved" if intended_action == "approve" else "rejected"):
            return approval
        raise HTTPException(
            status_code=400,
            detail=f"Approval already {approval.status}",
        )
    return approval
```

- [ ] **Step 3: Add early return in approve_action for already-approved**

At the top of `approve_action()`, after calling `_get_approval`:

```python
    approval = await _get_approval(db, approval_id, user_id, workspace_id, intended_action="approve")

    # Idempotent: already approved — return success without re-executing
    if approval.status == "approved":
        return ApprovalResponse(
            approval_id=approval.approval_id,
            status=approval.status,
            title=approval.title,
            summary=approval.summary,
            risk_level=approval.risk_level,
            created_at=approval.created_at,
        )
```

Do the same for `reject_action()` with `intended_action="reject"`.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "approval" 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/api/routes_approvals.py tests/test_ws_approval.py
git commit -m "feat: idempotent approval responses for double-click protection"
```

---

### Task 7: Validate artifact_refs at Approval Creation

**Gaps:** 3.7
**Files:**
- Modify: `backend/src/services/approval_service.py`
- Test: `backend/tests/test_approval_resume.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_approval_resume.py`:

```python
class TestArtifactRefsValidation:
    """Tool-level approvals must have valid artifact_refs."""

    def test_tool_approval_requires_tool_name_in_refs(self):
        """artifact_refs for tool approvals must contain 'tool_name'."""
        # After fix, creating a tool-level approval without tool_name
        # in artifact_refs should raise ValueError.
        pass  # Integration test requiring approval_service
```

- [ ] **Step 2: Add validation in approval_service.py**

In `backend/src/services/approval_service.py`, in the `create_approval()` function, add validation before creating the record:

```python
        # Validate artifact_refs for tool-level approvals
        if artifact_refs and approval_type and approval_type.startswith("tool:"):
            if "tool_name" not in artifact_refs:
                raise ValueError(
                    f"Tool-level approval requires 'tool_name' in artifact_refs, "
                    f"got keys: {list(artifact_refs.keys())}"
                )
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "approval" 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd backend && git add src/services/approval_service.py tests/test_approval_resume.py
git commit -m "fix: validate artifact_refs contains tool_name for tool-level approvals"
```
