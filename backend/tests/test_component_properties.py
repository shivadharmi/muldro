"""Tests for typed A2UI component property models."""

import pytest
from pydantic import BaseModel, ValidationError

from src.ui.component_properties import (
    PROPERTY_MODELS,
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


# ── Registry tests ───────────────────────────────────────────────────────────


def test_registry_has_exactly_23_entries():
    assert len(PROPERTY_MODELS) == 23


def test_layout_containers_not_in_registry():
    layout_containers = {"Card", "Row", "Column", "List", "Divider", "Form"}
    for container in layout_containers:
        assert container not in PROPERTY_MODELS, f"{container} should not be in PROPERTY_MODELS"


def test_all_registry_entries_are_basemodel_subclasses():
    for name, model_cls in PROPERTY_MODELS.items():
        assert issubclass(model_cls, BaseModel), f"{name} is not a BaseModel subclass"


# ── TextProperties ───────────────────────────────────────────────────────────


def test_text_properties_valid():
    props = TextProperties(text="Hello world")
    assert props.text == "Hello world"
    assert props.variant == "body"


def test_text_properties_all_variants():
    for variant in ("heading", "body", "caption"):
        props = TextProperties(text="Test", variant=variant)
        assert props.variant == variant


def test_text_properties_invalid_variant():
    with pytest.raises(ValidationError):
        TextProperties(text="Test", variant="invalid")


def test_text_properties_missing_required_text():
    with pytest.raises(ValidationError):
        TextProperties()


def test_text_properties_extra_fields_ignored():
    props = TextProperties(text="Hello", unknown_field="ignored")
    assert not hasattr(props, "unknown_field")


# ── ButtonProperties ─────────────────────────────────────────────────────────


def test_button_properties_valid():
    props = ButtonProperties(label="Click me")
    assert props.label == "Click me"
    assert props.variant == "primary"


def test_button_properties_all_variants():
    for variant in ("primary", "secondary", "danger", "ghost"):
        props = ButtonProperties(label="Btn", variant=variant)
        assert props.variant == variant


def test_button_properties_invalid_variant():
    with pytest.raises(ValidationError):
        ButtonProperties(label="Btn", variant="outline")


def test_button_properties_missing_required_label():
    with pytest.raises(ValidationError):
        ButtonProperties()


# ── BadgeProperties ──────────────────────────────────────────────────────────


def test_badge_properties_valid():
    props = BadgeProperties(label="Active")
    assert props.label == "Active"
    assert props.variant == "default"


def test_badge_properties_all_variants():
    for variant in ("default", "success", "warning", "danger"):
        props = BadgeProperties(label="X", variant=variant)
        assert props.variant == variant


def test_badge_properties_invalid_variant():
    with pytest.raises(ValidationError):
        BadgeProperties(label="X", variant="info")


# ── TableProperties ──────────────────────────────────────────────────────────


def test_table_properties_valid():
    columns = [{"key": "name", "label": "Name"}]
    rows = [{"name": "Alice"}]
    props = TableProperties(columns=columns, rows=rows)
    assert props.columns == columns
    assert props.rows == rows
    assert props.sortable is False


def test_table_properties_sortable():
    props = TableProperties(
        columns=[{"key": "id"}], rows=[{"id": 1}], sortable=True
    )
    assert props.sortable is True


def test_table_properties_missing_required_fields():
    with pytest.raises(ValidationError):
        TableProperties(columns=[{"key": "id"}])  # missing rows
    with pytest.raises(ValidationError):
        TableProperties(rows=[{"id": 1}])  # missing columns


# ── MetricProperties ─────────────────────────────────────────────────────────


def test_metric_properties_string_value():
    props = MetricProperties(label="Revenue", value="$1.2M")
    assert props.label == "Revenue"
    assert props.value == "$1.2M"
    assert props.change is None
    assert props.trend is None


def test_metric_properties_int_value():
    props = MetricProperties(label="Users", value=1000)
    assert props.value == 1000


def test_metric_properties_float_value():
    props = MetricProperties(label="Score", value=9.5)
    assert props.value == 9.5


def test_metric_properties_with_change_and_trend():
    props = MetricProperties(label="MRR", value="50K", change="+5%", trend="up")
    assert props.change == "+5%"
    assert props.trend == "up"


def test_metric_properties_missing_required_fields():
    with pytest.raises(ValidationError):
        MetricProperties(label="Revenue")  # missing value
    with pytest.raises(ValidationError):
        MetricProperties(value="100")  # missing label


# ── Default value tests ──────────────────────────────────────────────────────


def test_code_block_defaults():
    props = CodeBlockProperties(code="print('hi')")
    assert props.language == "text"


def test_alert_defaults():
    props = AlertProperties(message="Watch out")
    assert props.severity == "info"
    assert props.title is None


def test_text_field_all_defaults():
    props = TextFieldProperties()
    assert props.label == ""
    assert props.placeholder == ""
    assert props.value == ""


def test_select_defaults():
    props = SelectProperties(label="Choose")
    assert props.options == []
    assert props.value == ""


def test_toggle_defaults():
    props = ToggleProperties(label="Enable")
    assert props.checked is False


def test_data_grid_defaults():
    props = DataGridProperties(columns=[{"key": "id"}], rows=[])
    assert props.page_size == 20


def test_progress_defaults():
    props = ProgressProperties(value=42.0)
    assert props.max == 100
    assert props.label is None


def test_avatar_defaults():
    props = AvatarProperties(name="Alice")
    assert props.url is None
    assert props.size == "md"


def test_status_indicator_defaults():
    props = StatusIndicatorProperties(status="active")
    assert props.label == ""


def test_entity_card_defaults():
    props = EntityCardProperties(name="Acme Corp", entity_type="company")
    assert props.entity_id == ""
    assert props.attributes is None


def test_memory_card_defaults():
    props = MemoryCardProperties(fact_text="User prefers dark mode", memory_type="preference")
    assert props.source == ""
    assert props.confidence == 1.0


def test_execution_trace_defaults():
    props = ExecutionTraceProperties(steps=[{"id": "step_1"}])
    assert props.status == "running"


def test_calendar_defaults():
    props = CalendarProperties(events=[])
    assert props.view == "week"


def test_tabs_defaults():
    props = TabsProperties(labels=["Tab A", "Tab B"])
    assert props.active_tab == 0


def test_modal_defaults():
    props = ModalProperties(title="Confirm")
    assert props.open is True


def test_chart_defaults():
    props = ChartProperties(chart_type="bar", data={"labels": [], "values": []})
    assert props.title == ""


# ── Additional valid construction tests ─────────────────────────────────────


def test_timeline_properties_valid():
    props = TimelineProperties(events=[{"date": "2026-01-01", "title": "Launch"}])
    assert len(props.events) == 1


def test_kanban_board_properties_valid():
    props = KanbanBoardProperties(columns=[{"id": "todo", "title": "To Do", "cards": []}])
    assert len(props.columns) == 1


def test_modal_properties_closed():
    props = ModalProperties(title="Delete?", open=False)
    assert props.open is False


def test_avatar_all_sizes():
    for size in ("sm", "md", "lg"):
        props = AvatarProperties(name="Bob", size=size)
        assert props.size == size


def test_calendar_all_views():
    for view in ("day", "week", "month"):
        props = CalendarProperties(events=[], view=view)
        assert props.view == view
