"""Tests for A2UI component property models and A2UIComponent validation."""

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
    PROPERTY_MODELS,
    SelectProperties,
    StatusIndicatorProperties,
    TableProperties,
    TabsProperties,
    TextFieldProperties,
    TextProperties,
    TimelineProperties,
    ToggleProperties,
)


class TestTextProperties:
    def test_valid_text(self):
        p = TextProperties(text="Hello")
        assert p.text == "Hello"
        assert p.variant == "body"

    def test_custom_variant(self):
        p = TextProperties(text="Title", variant="heading")
        assert p.variant == "heading"

    def test_missing_text_raises(self):
        with pytest.raises(ValidationError):
            TextProperties()

    def test_invalid_variant_raises(self):
        with pytest.raises(ValidationError):
            TextProperties(text="x", variant="giant")


class TestCodeBlockProperties:
    def test_valid_code_block(self):
        p = CodeBlockProperties(code="print('hi')", language="python")
        assert p.language == "python"

    def test_missing_code_raises(self):
        with pytest.raises(ValidationError):
            CodeBlockProperties(language="python")

    def test_default_language(self):
        p = CodeBlockProperties(code="x = 1")
        assert p.language == "text"


class TestBadgeProperties:
    def test_valid_badge(self):
        p = BadgeProperties(label="Active", variant="success")
        assert p.label == "Active"

    def test_missing_label_raises(self):
        with pytest.raises(ValidationError):
            BadgeProperties(variant="success")

    def test_invalid_variant_raises(self):
        with pytest.raises(ValidationError):
            BadgeProperties(label="x", variant="neon")

    def test_default_variant(self):
        p = BadgeProperties(label="x")
        assert p.variant == "default"


class TestAlertProperties:
    def test_valid_alert(self):
        p = AlertProperties(message="Danger!", severity="error")
        assert p.severity == "error"

    def test_missing_message_raises(self):
        with pytest.raises(ValidationError):
            AlertProperties(severity="info")

    def test_invalid_severity_raises(self):
        with pytest.raises(ValidationError):
            AlertProperties(message="x", severity="critical")

    def test_optional_title(self):
        p = AlertProperties(message="x", title="My Alert")
        assert p.title == "My Alert"

    def test_default_severity(self):
        p = AlertProperties(message="x")
        assert p.severity == "info"


class TestButtonProperties:
    def test_valid_button(self):
        p = ButtonProperties(label="Click", variant="primary")
        assert p.label == "Click"

    def test_missing_label_raises(self):
        with pytest.raises(ValidationError):
            ButtonProperties(variant="primary")

    def test_invalid_variant_raises(self):
        with pytest.raises(ValidationError):
            ButtonProperties(label="x", variant="neon")

    def test_default_variant(self):
        p = ButtonProperties(label="x")
        assert p.variant == "primary"


class TestTextFieldProperties:
    def test_valid_text_field(self):
        p = TextFieldProperties(label="Name", placeholder="Enter name")
        assert p.label == "Name"

    def test_missing_label_raises(self):
        with pytest.raises(ValidationError):
            TextFieldProperties()

    def test_defaults(self):
        p = TextFieldProperties(label="x")
        assert p.placeholder == ""
        assert p.value == ""


class TestSelectProperties:
    def test_valid_select(self):
        p = SelectProperties(label="Color", options=[{"value": "red", "label": "Red"}])
        assert p.label == "Color"

    def test_missing_label_raises(self):
        with pytest.raises(ValidationError):
            SelectProperties()

    def test_defaults(self):
        p = SelectProperties(label="x")
        assert p.options == []
        assert p.value == ""


class TestToggleProperties:
    def test_valid_toggle(self):
        p = ToggleProperties(label="Enable", checked=True)
        assert p.checked is True

    def test_missing_label_raises(self):
        with pytest.raises(ValidationError):
            ToggleProperties()

    def test_default_checked(self):
        p = ToggleProperties(label="x")
        assert p.checked is False


class TestTableProperties:
    def test_valid_table(self):
        p = TableProperties(columns=[{"key": "name"}], rows=[{"name": "Alice"}])
        assert len(p.rows) == 1

    def test_missing_columns_raises(self):
        with pytest.raises(ValidationError):
            TableProperties(rows=[])

    def test_default_sortable(self):
        p = TableProperties(columns=[], rows=[])
        assert p.sortable is False


class TestDataGridProperties:
    def test_valid_data_grid(self):
        p = DataGridProperties(columns=[{"key": "id"}], rows=[])
        assert p.page_size == 10

    def test_missing_columns_raises(self):
        with pytest.raises(ValidationError):
            DataGridProperties(rows=[])


class TestTimelineProperties:
    def test_valid_timeline(self):
        p = TimelineProperties(events=[{"date": "2026-01-01", "text": "Start"}])
        assert len(p.events) == 1

    def test_missing_events_raises(self):
        with pytest.raises(ValidationError):
            TimelineProperties()


class TestMetricProperties:
    def test_valid_metric(self):
        p = MetricProperties(label="Revenue", value=100)
        assert p.value == 100

    def test_string_value(self):
        p = MetricProperties(label="Status", value="OK")
        assert p.value == "OK"

    def test_missing_label_raises(self):
        with pytest.raises(ValidationError):
            MetricProperties(value=0)

    def test_optional_change_trend(self):
        p = MetricProperties(label="x", value=1, change="+5%", trend="up")
        assert p.trend == "up"


class TestProgressProperties:
    def test_valid_progress(self):
        p = ProgressProperties(value=75.0)
        assert p.value == 75.0
        assert p.max == 100.0

    def test_missing_value_raises(self):
        with pytest.raises(ValidationError):
            ProgressProperties()

    def test_optional_label(self):
        p = ProgressProperties(value=50, label="Loading")
        assert p.label == "Loading"


class TestChartProperties:
    def test_valid_chart(self):
        p = ChartProperties(chart_type="bar", data={"labels": [], "values": []})
        assert p.chart_type == "bar"

    def test_missing_chart_type_raises(self):
        with pytest.raises(ValidationError):
            ChartProperties(data={})

    def test_missing_data_raises(self):
        with pytest.raises(ValidationError):
            ChartProperties(chart_type="line")


class TestAvatarProperties:
    def test_valid_avatar(self):
        p = AvatarProperties(name="Alice")
        assert p.name == "Alice"
        assert p.size == "md"

    def test_missing_name_raises(self):
        with pytest.raises(ValidationError):
            AvatarProperties()

    def test_invalid_size_raises(self):
        with pytest.raises(ValidationError):
            AvatarProperties(name="x", size="xl")


class TestStatusIndicatorProperties:
    def test_valid_status(self):
        p = StatusIndicatorProperties(status="running", label="Running")
        assert p.label == "Running"

    def test_missing_fields_raise(self):
        with pytest.raises(ValidationError):
            StatusIndicatorProperties(status="ok")


class TestEntityCardProperties:
    def test_valid_entity_card(self):
        p = EntityCardProperties(name="Acme Corp", entity_type="company", entity_id="ent_01")
        assert p.entity_id == "ent_01"

    def test_missing_required_raises(self):
        with pytest.raises(ValidationError):
            EntityCardProperties(name="x", entity_type="company")

    def test_optional_attributes(self):
        p = EntityCardProperties(
            name="x", entity_type="y", entity_id="z", attributes={"revenue": "1M"}
        )
        assert p.attributes == {"revenue": "1M"}


class TestMemoryCardProperties:
    def test_valid_memory_card(self):
        p = MemoryCardProperties(
            fact_text="User prefers dark mode",
            memory_type="preference",
            source="chat",
        )
        assert p.confidence == 1.0

    def test_missing_fact_text_raises(self):
        with pytest.raises(ValidationError):
            MemoryCardProperties(memory_type="preference", source="chat")


class TestExecutionTraceProperties:
    def test_valid_trace(self):
        p = ExecutionTraceProperties(steps=[{"name": "step1"}], status="completed")
        assert p.status == "completed"

    def test_missing_steps_raises(self):
        with pytest.raises(ValidationError):
            ExecutionTraceProperties(status="running")


class TestKanbanBoardProperties:
    def test_valid_kanban(self):
        p = KanbanBoardProperties(columns=[{"title": "Todo", "cards": []}])
        assert len(p.columns) == 1

    def test_missing_columns_raises(self):
        with pytest.raises(ValidationError):
            KanbanBoardProperties()


class TestCalendarProperties:
    def test_valid_calendar(self):
        p = CalendarProperties(events=[{"date": "2026-01-01"}])
        assert p.view == "week"

    def test_missing_events_raises(self):
        with pytest.raises(ValidationError):
            CalendarProperties()

    def test_invalid_view_raises(self):
        with pytest.raises(ValidationError):
            CalendarProperties(events=[], view="year")


class TestTabsProperties:
    def test_valid_tabs(self):
        p = TabsProperties(labels=["Tab A", "Tab B"])
        assert p.active_tab == 0

    def test_missing_labels_raises(self):
        with pytest.raises(ValidationError):
            TabsProperties()

    def test_custom_active_tab(self):
        p = TabsProperties(labels=["A", "B", "C"], active_tab=2)
        assert p.active_tab == 2


class TestModalProperties:
    def test_valid_modal(self):
        p = ModalProperties(title="Confirm")
        assert p.open is False

    def test_missing_title_raises(self):
        with pytest.raises(ValidationError):
            ModalProperties()

    def test_open_true(self):
        p = ModalProperties(title="x", open=True)
        assert p.open is True


class TestPropertyModelsRegistry:
    def test_registry_has_22_entries(self):
        assert len(PROPERTY_MODELS) == 23  # 22 per spec + Tabs and Modal = 23

    def test_all_registry_values_are_base_model_subclasses(self):
        from pydantic import BaseModel

        for name, model in PROPERTY_MODELS.items():
            assert issubclass(model, BaseModel), f"{name} is not a BaseModel subclass"

    def test_layout_containers_absent(self):
        """Card, Row, Column, List, Divider, Form are not in registry."""
        for layout_type in ("Card", "Row", "Column", "List", "Divider", "Form"):
            assert layout_type not in PROPERTY_MODELS


class TestA2UIComponentValidation:
    def test_valid_button_passes_validation(self):
        from src.ui.contracts import A2UIComponent

        c = A2UIComponent(
            type="Button", id="btn1", properties={"label": "Click", "variant": "primary"}
        )
        assert c.type == "Button"

    def test_button_missing_label_rejected(self):
        from src.ui.contracts import A2UIComponent

        with pytest.raises(ValidationError):
            A2UIComponent(type="Button", id="btn1", properties={"variant": "primary"})

    def test_button_invalid_variant_rejected(self):
        from src.ui.contracts import A2UIComponent

        with pytest.raises(ValidationError):
            A2UIComponent(type="Button", id="btn1", properties={"label": "OK", "variant": "neon"})

    def test_text_valid(self):
        from src.ui.contracts import A2UIComponent

        c = A2UIComponent(type="Text", id="t1", properties={"text": "Hello"})
        assert c.properties["text"] == "Hello"

    def test_layout_container_skips_validation(self):
        from src.ui.contracts import A2UIComponent

        c = A2UIComponent(type="Card", id="c1", properties={})
        assert c.type == "Card"

    def test_unknown_type_skips_validation(self):
        from src.ui.contracts import A2UIComponent

        c = A2UIComponent(type="FutureWidget", id="fw1", properties={"anything": True})
        assert c.type == "FutureWidget"
