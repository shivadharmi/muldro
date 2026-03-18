"""Tests for A2UI view generators and expanded renderer."""

from src.ui import renderer as r
from src.ui.contracts import A2UISurface
from src.ui.views import (
    dashboard_view,
    entity_explorer_view,
    execution_trace_view,
    memory_browser_view,
    task_board_view,
)
from tests.conftest import TEST_USER_ID


class TestRendererNewComponents:
    def test_table(self):
        t = r.table(
            "t1",
            [{"key": "name", "label": "Name"}],
            [{"name": "Alice"}],
            sortable=True,
        )
        assert t.type == "Table"
        assert t.properties["sortable"] is True
        assert len(t.properties["rows"]) == 1

    def test_timeline(self):
        tl = r.timeline("tl1", [{"time": "09:00", "title": "Meeting"}])
        assert tl.type == "Timeline"
        assert len(tl.properties["events"]) == 1

    def test_metric(self):
        m = r.metric("m1", "Active Tasks", 5, change="+2", trend="up")
        assert m.type == "Metric"
        assert m.properties["value"] == 5
        assert m.properties["trend"] == "up"

    def test_progress(self):
        p = r.progress("p1", 75, 100, label="75%")
        assert p.type == "Progress"
        assert p.properties["value"] == 75

    def test_chart(self):
        c = r.chart("c1", "bar", {"labels": ["A"], "values": [10]})
        assert c.type == "Chart"
        assert c.properties["chart_type"] == "bar"

    def test_badge(self):
        b = r.badge("b1", "high", variant="red")
        assert b.type == "Badge"
        assert b.properties["label"] == "high"

    def test_alert(self):
        a = r.alert("a1", "Something happened", "warning", title="Alert")
        assert a.type == "Alert"
        assert a.properties["severity"] == "warning"
        assert a.properties["title"] == "Alert"

    def test_code_block(self):
        cb = r.code_block("cb1", "print('hello')", "python")
        assert cb.type == "CodeBlock"
        assert cb.properties["language"] == "python"

    def test_avatar(self):
        a = r.avatar("av1", "John", size="lg")
        assert a.type == "Avatar"
        assert a.properties["name"] == "John"

    def test_status_indicator(self):
        s = r.status_indicator("si1", "healthy", label="Gmail")
        assert s.type == "StatusIndicator"
        assert s.properties["status"] == "healthy"

    def test_entity_card(self):
        ec = r.entity_card("ec1", "Alice", "person", "ent_123")
        assert ec.type == "EntityCard"
        assert ec.properties["entity_type"] == "person"

    def test_memory_card(self):
        mc = r.memory_card("mc1", "Prefers morning meetings", "preference")
        assert mc.type == "MemoryCard"
        assert mc.properties["memory_type"] == "preference"

    def test_execution_trace(self):
        et = r.execution_trace("et1", [{"step": "research", "status": "completed"}])
        assert et.type == "ExecutionTrace"

    def test_kanban_board(self):
        kb = r.kanban_board("kb1", [{"title": "Todo", "items": []}])
        assert kb.type == "KanbanBoard"

    def test_calendar_view(self):
        cv = r.calendar_view("cv1", [{"title": "Meeting"}], view="month")
        assert cv.type == "Calendar"
        assert cv.properties["view"] == "month"

    def test_select_field(self):
        sf = r.select_field("sf1", "Choose", [{"value": "a", "label": "A"}])
        assert sf.type == "Select"

    def test_toggle(self):
        t = r.toggle("t1", "Enable", checked=True)
        assert t.type == "Toggle"
        assert t.properties["checked"] is True

    def test_form(self):
        f = r.form(
            "f1",
            [r.text_field("tf1", "Name")],
            submit_label="Save",
        )
        assert f.type == "Form"
        assert len(f.children) == 2  # field + submit

    def test_tabs(self):
        t = r.tabs(
            "tabs1",
            ["Tab A", "Tab B"],
            [[r.text("t1", "Content A")], [r.text("t2", "Content B")]],
        )
        assert t.type == "Tabs"
        assert len(t.children) == 2

    def test_modal(self):
        m = r.modal("m1", "Confirm", [r.text("mt", "Are you sure?")])
        assert m.type == "Modal"
        assert m.properties["open"] is True


class TestDashboardView:
    def test_generates_surface(self):
        s = dashboard_view(
            TEST_USER_ID,
            active_tasks=[{"task_id": "t1", "goal": "Test"}],
            pending_approvals=[],
            recent_events=[],
            budget={"used": 2.5, "limit": 5.0},
            connector_health=[],
        )
        assert isinstance(s, A2UISurface)
        assert "dashboard" in s.id

    def test_includes_metrics(self):
        s = dashboard_view(
            TEST_USER_ID,
            [{"task_id": "t1"}],
            [],
            [],
            {"used": 0},
            [],
        )
        # Should have heading + metrics row at minimum
        assert len(s.children) >= 2


class TestTaskBoardView:
    def test_generates_kanban(self):
        s = task_board_view(
            TEST_USER_ID,
            {
                "pending": [{"task_id": "t1", "goal": "Research"}],
                "running": [],
                "completed": [{"task_id": "t2", "goal": "Draft"}],
            },
        )
        assert isinstance(s, A2UISurface)
        kanban = [c for c in s.children if c.type == "KanbanBoard"]
        assert len(kanban) == 1


class TestExecutionTraceView:
    def test_generates_trace(self):
        s = execution_trace_view(
            "run_001",
            [
                {"step": "research", "status": "completed"},
                {"step": "draft", "status": "running"},
            ],
            "running",
        )
        assert isinstance(s, A2UISurface)
        trace = [c for c in s.children if c.type == "ExecutionTrace"]
        assert len(trace) == 1


class TestEntityExplorerView:
    def test_with_entities(self):
        s = entity_explorer_view(
            TEST_USER_ID,
            [
                {"canonical_name": "Alice", "entity_type": "person"},
            ],
        )
        entity_cards = [c for c in s.children if c.type == "List"]
        assert len(entity_cards) == 1

    def test_empty(self):
        s = entity_explorer_view(TEST_USER_ID, [])
        texts = [c for c in s.children if c.type == "Text"]
        assert any("No entities" in t.properties.get("text", "") for t in texts)


class TestMemoryBrowserView:
    def test_with_memories(self):
        s = memory_browser_view(
            TEST_USER_ID,
            [
                {"fact_text": "Prefers email", "memory_type": "preference"},
            ],
        )
        assert isinstance(s, A2UISurface)
