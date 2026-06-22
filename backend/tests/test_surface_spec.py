"""Tests for SurfaceSpec contract and parser."""

import pytest
from pydantic import ValidationError


class TestSurfaceSpec:
    def test_valid_spec(self):
        from src.contracts import SurfaceSpec

        spec = SurfaceSpec(
            should_surface=True,
            kind="summary",
            title="Email Summary",
            subtitle="3 unread emails from today",
        )
        assert spec.should_surface is True
        assert spec.kind == "summary"

    def test_title_capped_at_80(self):
        from src.contracts import SurfaceSpec

        spec = SurfaceSpec(should_surface=True, kind="plan", title="A" * 200)
        assert len(spec.title) == 80

    def test_subtitle_capped_at_120(self):
        from src.contracts import SurfaceSpec

        spec = SurfaceSpec(should_surface=True, kind="plan", title="Test", subtitle="B" * 200)
        assert len(spec.subtitle) == 120

    def test_invalid_kind_rejected(self):
        from src.contracts import SurfaceSpec

        with pytest.raises(ValidationError):
            SurfaceSpec(should_surface=True, kind="invalid_kind", title="Test")

    def test_metrics_default_empty(self):
        from src.contracts import SurfaceSpec

        spec = SurfaceSpec(should_surface=True, kind="table", title="Data")
        assert spec.metrics == []
        assert spec.tags == []

    def test_none_subtitle_unchanged(self):
        from src.contracts import SurfaceSpec

        spec = SurfaceSpec(should_surface=True, kind="summary", title="Test")
        assert spec.subtitle is None


class TestExtractSurfaceSpec:
    def test_extracts_valid_json_block(self):
        from src.services.surface_mapping import extract_surface_spec

        text = """Here is your summary.

```json:surface
{"should_surface": true, "kind": "summary", "title": "Email Summary"}
```

The key finding is..."""

        spec = extract_surface_spec(text)
        assert spec is not None
        assert spec.kind == "summary"
        assert spec.title == "Email Summary"

    def test_returns_none_when_no_block(self):
        from src.services.surface_mapping import extract_surface_spec

        spec = extract_surface_spec("Just a plain response with no surface.")
        assert spec is None

    def test_returns_none_on_malformed_json(self):
        from src.services.surface_mapping import extract_surface_spec

        text = """Response.

```json:surface
{invalid json here}
```"""

        spec = extract_surface_spec(text)
        assert spec is None

    def test_should_surface_false_still_parses(self):
        from src.services.surface_mapping import extract_surface_spec

        text = """Response.

```json:surface
{"should_surface": false, "kind": "summary", "title": "Test"}
```"""

        spec = extract_surface_spec(text)
        assert spec is not None
        assert spec.should_surface is False

    def test_returns_none_on_invalid_kind(self):
        from src.services.surface_mapping import extract_surface_spec

        text = """Response.

```json:surface
{"should_surface": true, "kind": "bogus", "title": "Test"}
```"""

        spec = extract_surface_spec(text)
        assert spec is None


class TestExtractSurfaceData:
    """extract_surface_data returns a typed SurfaceDataPayload whose sections
    are validated A2UIComponent trees."""

    def test_extracts_typed_sections(self):
        from src.services.surface_mapping import extract_surface_data

        text = """Response.

```json:surface_data
{"sections": [
  {"type": "Text", "id": "t1", "properties": {"text": "Hello", "variant": "heading"}},
  {"type": "Table", "id": "t2", "properties": {
    "columns": [{"key": "title", "label": "Title"}],
    "rows": [{"title": "Fix bug"}]
  }}
]}
```"""

        data = extract_surface_data(text)
        assert data is not None
        assert len(data.sections) == 2
        assert data.sections[0].type == "Text"
        assert data.sections[1].type == "Table"
        assert data.sections[1].properties["rows"] == [{"title": "Fix bug"}]

    def test_returns_none_when_no_data_block(self):
        from src.services.surface_mapping import extract_surface_data

        data = extract_surface_data("No data block here.")
        assert data is None

    def test_returns_none_on_malformed_data_json(self):
        from src.services.surface_mapping import extract_surface_data

        text = """Response.

```json:surface_data
{not valid json}
```"""

        data = extract_surface_data(text)
        assert data is None

    def test_returns_none_when_section_type_invalid_properties(self):
        """Properties that fail the per-type Pydantic model reject the whole payload."""
        from src.services.surface_mapping import extract_surface_data

        # Text requires "text" in properties; missing it should invalidate.
        text = """```json:surface_data
{"sections": [{"type": "Text", "id": "t1", "properties": {}}]}
```"""

        data = extract_surface_data(text)
        assert data is None

    def test_empty_sections_is_valid(self):
        """Empty sections list parses fine — no structured content."""
        from src.services.surface_mapping import extract_surface_data

        text = """```json:surface_data
{"sections": []}
```"""

        data = extract_surface_data(text)
        assert data is not None
        assert data.sections == []


class TestStripSurfaceBlocks:
    """strip_surface_blocks removes both ```json:surface``` and
    ```json:surface_data``` fenced blocks from user-facing chat text."""

    def test_strips_surface_block(self):
        from src.services.surface_mapping import strip_surface_blocks

        text = """Hello, here is a summary.

```json:surface
{"should_surface": true, "kind": "summary", "title": "Test"}
```

More text follows."""
        stripped = strip_surface_blocks(text)
        assert "json:surface" not in stripped
        assert "Hello, here is a summary." in stripped
        assert "More text follows." in stripped

    def test_strips_both_blocks(self):
        from src.services.surface_mapping import strip_surface_blocks

        text = """Intro.

```json:surface
{"should_surface": true, "kind": "table", "title": "PRs"}
```

```json:surface_data
{"sections": []}
```

Outro."""
        stripped = strip_surface_blocks(text)
        assert "json:surface" not in stripped
        assert "json:surface_data" not in stripped
        assert "Intro." in stripped
        assert "Outro." in stripped
        # Blank-line collapsing means no triple-newline run remains.
        assert "\n\n\n" not in stripped

    def test_no_op_when_no_blocks(self):
        from src.services.surface_mapping import strip_surface_blocks

        text = "Just plain text — no fenced blocks here."
        assert strip_surface_blocks(text) == text

    def test_empty_input_is_safe(self):
        from src.services.surface_mapping import strip_surface_blocks

        assert strip_surface_blocks("") == ""
        assert strip_surface_blocks(None) is None  # type: ignore[arg-type]


class TestSurfaceDataPayloadContract:
    """SurfaceDataPayload wraps validated A2UIComponent sections."""

    def test_accepts_valid_components(self):
        from src.contracts import SurfaceDataPayload

        payload = SurfaceDataPayload(
            sections=[
                {
                    "type": "Text",
                    "id": "greet",
                    "properties": {"text": "Hello world", "variant": "body"},
                },
                {
                    "type": "Timeline",
                    "id": "tl",
                    "properties": {
                        "events": [{"time": "2026-Q1", "title": "Seed"}],
                    },
                },
            ]
        )
        assert len(payload.sections) == 2
        assert payload.sections[0].type == "Text"

    def test_rejects_component_with_invalid_properties(self):
        """A Table section missing required 'columns' / 'rows' is rejected."""
        from src.contracts import SurfaceDataPayload

        with pytest.raises(ValidationError):
            SurfaceDataPayload(
                sections=[
                    {"type": "Table", "id": "t1", "properties": {"rows": []}},
                ]
            )
