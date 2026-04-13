"""Tests for history API response schemas and endpoints."""

from datetime import datetime, timezone


class TestHistorySchemas:
    def test_history_step_response_shape(self):
        from src.api.schemas_history import HistoryStepSummary

        step = HistoryStepSummary(
            step_id="step_001",
            name="Search emails",
            capability="email.search",
            status="completed",
            started_at=datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc),
        )
        assert step.step_id == "step_001"
        assert step.status == "completed"

    def test_history_item_response_shape(self):
        from src.api.schemas_history import HistoryItemResponse

        item = HistoryItemResponse(
            run_id="run_001",
            plan_id="plan_001",
            goal="Send investor email",
            source="background",
            trigger_type="event",
            status="completed",
            risk_level=None,
            started_at=datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 18, tzinfo=timezone.utc),
            error=None,
            retry_count=0,
            step_count=3,
            completed_step_count=3,
            cost_usd=0.004,
            steps=[],
            approval=None,
            live_phase=None,
            surface_id=None,
        )
        assert item.run_id == "run_001"
        assert item.step_count == 3

    def test_history_list_response_shape(self):
        from src.api.schemas_history import HistoryListResponse

        resp = HistoryListResponse(items=[], total=0, limit=20, offset=0)
        assert resp.total == 0
        assert resp.limit == 20

    def test_history_detail_step_includes_output(self):
        from src.api.schemas_history import HistoryDetailStep

        step = HistoryDetailStep(
            step_id="step_001",
            name="Search emails",
            capability="email.search",
            status="completed",
            input_data={"query": "investor"},
            output_data={"result": "Found 3 threads"},
            started_at=datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc),
            duration_ms=2340,
            error=None,
            artifacts=[],
        )
        assert step.output_data == {"result": "Found 3 threads"}
        assert step.duration_ms == 2340
