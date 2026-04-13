"""Tests for SurfaceSpec contract and parser."""

import pytest
from pydantic import ValidationError


class TestSurfaceSpec:
    def test_valid_spec(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(
            should_surface=True,
            kind="summary",
            title="Email Summary",
            subtitle="3 unread emails from today",
        )
        assert spec.should_surface is True
        assert spec.kind == "summary"

    def test_title_capped_at_80(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(should_surface=True, kind="plan", title="A" * 200)
        assert len(spec.title) == 80

    def test_subtitle_capped_at_120(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(
            should_surface=True, kind="plan", title="Test", subtitle="B" * 200
        )
        assert len(spec.subtitle) == 120

    def test_invalid_kind_rejected(self):
        from src.orchestrator.contracts import SurfaceSpec

        with pytest.raises(ValidationError):
            SurfaceSpec(should_surface=True, kind="invalid_kind", title="Test")

    def test_metrics_default_empty(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(should_surface=True, kind="table", title="Data")
        assert spec.metrics == []
        assert spec.tags == []

    def test_none_subtitle_unchanged(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(should_surface=True, kind="summary", title="Test")
        assert spec.subtitle is None


class TestExtractSurfaceSpec:
    def test_extracts_valid_json_block(self):
        from src.services.surface_mapping import extract_surface_spec

        text = '''Here is your summary.

```json:surface
{"should_surface": true, "kind": "summary", "title": "Email Summary"}
```

The key finding is...'''

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

        text = '''Response.

```json:surface
{invalid json here}
```'''

        spec = extract_surface_spec(text)
        assert spec is None

    def test_should_surface_false_still_parses(self):
        from src.services.surface_mapping import extract_surface_spec

        text = '''Response.

```json:surface
{"should_surface": false, "kind": "summary", "title": "Test"}
```'''

        spec = extract_surface_spec(text)
        assert spec is not None
        assert spec.should_surface is False

    def test_returns_none_on_invalid_kind(self):
        from src.services.surface_mapping import extract_surface_spec

        text = '''Response.

```json:surface
{"should_surface": true, "kind": "bogus", "title": "Test"}
```'''

        spec = extract_surface_spec(text)
        assert spec is None


class TestExtractSurfaceData:
    def test_extracts_surface_data_block(self):
        from src.services.surface_mapping import extract_surface_data

        text = '''Response.

```json:surface
{"should_surface": true, "kind": "table", "title": "PRs"}
```

```json:surface_data
{"columns": [{"key": "title"}], "rows": [{"title": "Fix bug"}]}
```'''

        data = extract_surface_data(text)
        assert data is not None
        assert "columns" in data
        assert len(data["rows"]) == 1

    def test_returns_none_when_no_data_block(self):
        from src.services.surface_mapping import extract_surface_data

        data = extract_surface_data("No data block here.")
        assert data is None

    def test_returns_none_on_malformed_data_json(self):
        from src.services.surface_mapping import extract_surface_data

        text = '''Response.

```json:surface_data
{not valid json}
```'''

        data = extract_surface_data(text)
        assert data is None
