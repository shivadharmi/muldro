"""Tests for component_properties.py typed property models and renderer.py builder integration."""

import pytest
from pydantic import ValidationError

from src.ui.component_properties import (
    AlertProperties,
    AvatarProperties,
    BadgeProperties,
    ButtonProperties,
    CalendarProperties,
    ChartProperties,
    CodeBlockProperties,
    DataGridProperties,
    EntityCardProperties,
    ExecutionTraceProperties,
    KanbanBoardProperties,
    MemoryCardProperties,
    MetricProperties,
    ModalProperties,
    ProgressProperties,
    SelectProperties,
    StatusIndicatorProperties,
    TableProperties,
    TabsProperties,
    TextFieldProperties,
    TextProperties,
    TimelineProperties,
    ToggleProperties,
)
from src.ui.renderer import (
    alert,
    avatar,
    badge,
    button,
    calendar_view,
    caption,
    chart,
    code_block,
    data_grid,
    entity_card,
    execution_trace,
    heading,
    kanban_board,
    memory_card,
    metric,
    modal,
    progress,
    select_field,
    status_indicator,
    table,
    tabs,
    text,
    text_field,
    timeline,
    toggle,
)


# ── Property model validation tests ─────────────────────────────


class TestTextProperties:
    def test_default_variant(self):
        props = TextProperties(text="Hello")
        assert props.variant == "body"

    def test_valid_variants(self):
        for variant in ("body", "heading", "caption", "label", "subheading"):
            p = TextProperties(text="x", variant=variant)
            assert p.variant == variant

    def test_invalid_variant_raises(self):
        with pytest.raises(ValidationError):
            TextProperties(text="x", variant="invalid")

    def test_model_dump(self):
        props = TextProperties(text="hi", variant="caption")
        d = props.model_dump()
        assert d == {"text": "hi", "variant": "caption"}


class TestCodeBlockProperties:
    def test_defaults(self):
        props = CodeBlockProperties(code="print(1)")
        assert props.language == "text"

    def test_model_dump(self):
        props = CodeBlockProperties(code="x = 1", language="python")
        d = props.model_dump()
        assert d["code"] == "x = 1"
        assert d["language"] == "python"


class TestBadgeProperties:
    def test_defaults(self):
        props = BadgeProperties(label="New")
        assert props.variant == "default"

    def test_valid_variants(self):
        for v in ("default", "success", "warning", "danger", "info"):
            BadgeProperties(label="x", variant=v)

    def test_invalid_variant_raises(self):
        with pytest.raises(ValidationError):
            BadgeProperties(label="x", variant="unknown")


class TestAlertProperties:
    def test_defaults(self):
        props = AlertProperties(message="msg")
        assert props.severity == "info"
        assert props.title is None

    def test_with_title(self):
        props = AlertProperties(message="msg", severity="error", title="Oops")
        d = props.model_dump()
        assert d["title"] == "Oops"

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            AlertProperties(message="m", severity="critical")


class TestTabsProperties:
    def test_defaults(self):
        props = TabsProperties()
        assert props.active_tab == 0
        assert props.labels == []

    def test_with_labels(self):
        props = TabsProperties(active_tab=1, labels=["A", "B"])
        assert props.labels == ["A", "B"]


class TestModalProperties:
    def test_defaults(self):
        props = ModalProperties(title="My Modal")
        assert props.open is True

    def test_closed(self):
        props = ModalProperties(title="x", open=False)
        assert props.open is False


class TestButtonProperties:
    def test_defaults(self):
        props = ButtonProperties(label="Click me")
        assert props.variant == "primary"

    def test_valid_variants(self):
        for v in ("primary", "secondary", "danger", "ghost"):
            ButtonProperties(label="x", variant=v)

    def test_invalid_variant(self):
        with pytest.raises(ValidationError):
            ButtonProperties(label="x", variant="link")


class TestMetricProperties:
    def test_optional_fields(self):
        props = MetricProperties(label="Revenue", value=1000)
        d = props.model_dump()
        assert d["change"] is None
        assert d["trend"] is None

    def test_with_change_and_trend(self):
        props = MetricProperties(label="MRR", value=5000, change="+10%", trend="up")
        d = props.model_dump()
        assert d["change"] == "+10%"
        assert d["trend"] == "up"


class TestProgressProperties:
    def test_defaults(self):
        props = ProgressProperties(value=50)
        assert props.max == 100
        assert props.label is None

    def test_custom_max(self):
        props = ProgressProperties(value=3, max=10, label="Steps")
        d = props.model_dump()
        assert d["max"] == 10
        assert d["label"] == "Steps"


class TestAvatarProperties:
    def test_defaults(self):
        props = AvatarProperties(name="Alice")
        assert props.url is None
        assert props.size == "md"

    def test_valid_sizes(self):
        for s in ("sm", "md", "lg"):
            AvatarProperties(name="x", size=s)

    def test_invalid_size(self):
        with pytest.raises(ValidationError):
            AvatarProperties(name="x", size="xl")


class TestEntityCardProperties:
    def test_optional_attributes(self):
        props = EntityCardProperties(name="Acme", entity_type="company")
        assert props.attributes is None
        assert props.entity_id == ""

    def test_with_attributes(self):
        props = EntityCardProperties(
            name="Acme", entity_type="company", entity_id="ent_01", attributes={"stage": "Series A"}
        )
        d = props.model_dump()
        assert d["attributes"] == {"stage": "Series A"}


class TestCalendarProperties:
    def test_defaults(self):
        props = CalendarProperties()
        assert props.view == "week"
        assert props.events == []

    def test_valid_views(self):
        for v in ("day", "week", "month"):
            CalendarProperties(view=v)

    def test_invalid_view(self):
        with pytest.raises(ValidationError):
            CalendarProperties(view="year")


# ── Builder integration tests ────────────────────────────────────


class TestBuilderIntegration:
    """Test that builders produce valid A2UIComponent with correct properties."""

    def test_text_builder(self):
        c = text("t1", "Hello World")
        assert c.type == "Text"
        assert c.properties["text"] == "Hello World"
        assert c.properties["variant"] == "body"

    def test_heading_builder(self):
        c = heading("h1", "My Title")
        assert c.properties["variant"] == "heading"

    def test_caption_builder(self):
        c = caption("c1", "small note")
        assert c.properties["variant"] == "caption"

    def test_code_block_builder(self):
        c = code_block("cb1", "x = 1", language="python")
        assert c.type == "CodeBlock"
        assert c.properties["language"] == "python"

    def test_badge_builder(self):
        c = badge("b1", "Active", variant="success")
        assert c.type == "Badge"
        assert c.properties["label"] == "Active"
        assert c.properties["variant"] == "success"

    def test_alert_builder_with_title(self):
        c = alert("a1", "Something failed", severity="error", title="Error")
        assert c.type == "Alert"
        assert c.properties["severity"] == "error"
        assert c.properties["title"] == "Error"

    def test_alert_builder_without_title(self):
        c = alert("a2", "Info message")
        assert c.properties["title"] is None

    def test_button_builder_with_action(self):
        c = button("btn1", "Approve", "primary", {"action": "approve", "id": "apr_01"})
        assert c.type == "Button"
        assert len(c.actions) == 1
        assert c.actions[0].payload["action"] == "approve"

    def test_button_builder_no_action(self):
        c = button("btn2", "Cancel")
        assert c.actions == []

    def test_text_field_builder(self):
        c = text_field("tf1", label="Name", placeholder="Enter name")
        assert c.type == "TextField"
        assert c.properties["placeholder"] == "Enter name"

    def test_select_field_builder(self):
        opts = [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}]
        c = select_field("sf1", "Choose", opts, value="a")
        assert c.type == "Select"
        assert c.properties["options"] == opts

    def test_toggle_builder(self):
        c = toggle("tog1", "Enable feature", checked=True)
        assert c.type == "Toggle"
        assert c.properties["checked"] is True

    def test_table_builder(self):
        cols = [{"key": "name", "label": "Name"}]
        rows = [{"name": "Alice"}]
        c = table("tbl1", cols, rows, sortable=True)
        assert c.type == "Table"
        assert c.properties["sortable"] is True

    def test_data_grid_builder(self):
        c = data_grid("dg1", [], [], page_size=50)
        assert c.type == "DataGrid"
        assert c.properties["page_size"] == 50

    def test_timeline_builder(self):
        events = [{"date": "2026-01-01", "title": "Launch"}]
        c = timeline("tl1", events)
        assert c.type == "Timeline"
        assert c.properties["events"] == events

    def test_metric_builder(self):
        c = metric("m1", "MRR", 5000, change="+10%", trend="up")
        assert c.type == "Metric"
        assert c.properties["value"] == 5000
        assert c.properties["change"] == "+10%"

    def test_metric_builder_no_optional(self):
        c = metric("m2", "Users", 100)
        assert c.properties["change"] is None

    def test_progress_builder(self):
        c = progress("p1", 75.0, max_value=100, label="Done")
        assert c.type == "Progress"
        assert c.properties["value"] == 75.0
        assert c.properties["max"] == 100

    def test_chart_builder(self):
        c = chart("ch1", "bar", {"labels": ["Jan"], "data": [10]}, title="Revenue")
        assert c.type == "Chart"
        assert c.properties["chart_type"] == "bar"
        assert c.properties["title"] == "Revenue"

    def test_avatar_builder(self):
        c = avatar("av1", "Alice", url="https://example.com/a.png", size="lg")
        assert c.type == "Avatar"
        assert c.properties["url"] == "https://example.com/a.png"

    def test_avatar_builder_no_url(self):
        c = avatar("av2", "Bob")
        assert c.properties["url"] is None

    def test_status_indicator_builder(self):
        c = status_indicator("si1", "active", label="Running")
        assert c.type == "StatusIndicator"
        assert c.properties["status"] == "active"

    def test_entity_card_builder(self):
        c = entity_card("ec1", "Acme Corp", "company", "ent_01", {"mrr": 5000})
        assert c.type == "EntityCard"
        assert c.properties["entity_id"] == "ent_01"
        assert c.properties["attributes"] == {"mrr": 5000}

    def test_memory_card_builder(self):
        c = memory_card("mc1", "User prefers dark mode", "preference", confidence=0.9)
        assert c.type == "MemoryCard"
        assert c.properties["confidence"] == 0.9

    def test_execution_trace_builder(self):
        steps = [{"name": "step1", "status": "done"}]
        c = execution_trace("et1", steps, status="completed")
        assert c.type == "ExecutionTrace"
        assert c.properties["status"] == "completed"

    def test_kanban_board_builder(self):
        cols = [{"title": "Todo", "cards": []}]
        c = kanban_board("kb1", cols)
        assert c.type == "KanbanBoard"
        assert c.properties["columns"] == cols

    def test_calendar_view_builder(self):
        evts = [{"title": "Meeting", "start": "2026-04-14T10:00"}]
        c = calendar_view("cal1", evts, view="month")
        assert c.type == "Calendar"
        assert c.properties["view"] == "month"

    def test_tabs_builder(self):
        tab1 = text("t1", "Tab 1 content")
        tab2 = text("t2", "Tab 2 content")
        c = tabs("tabs1", ["Alpha", "Beta"], [[tab1], [tab2]], active_tab=1)
        assert c.type == "Tabs"
        assert c.properties["active_tab"] == 1
        assert c.properties["labels"] == ["Alpha", "Beta"]
        assert len(c.children) == 2
        assert c.children[0].properties["tab_label"] == "Alpha"

    def test_modal_builder(self):
        c = modal("m1", "Confirm Action", [text("t1", "Are you sure?")], open=True)
        assert c.type == "Modal"
        assert c.properties["title"] == "Confirm Action"
        assert c.properties["open"] is True
        assert len(c.children) == 1
