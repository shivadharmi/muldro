"""Tests for registry validation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.tools.validation import validate_registry


def test_validate_registry_passes():
    """Validation should pass with real data."""
    errors = validate_registry()
    assert errors == []


def test_unknown_capability_detected():
    """Should detect tool with unknown capability."""
    from dataclasses import dataclass

    from pydantic import BaseModel, Field

    @dataclass(frozen=True, slots=True)
    class MockToolDef:
        name: str
        capability: str
        risk_level: str = "low"
        requires_approval: bool = False
        read_only: bool = False

    class MockInput(BaseModel):
        test: str = Field(description="test")

    bad_internal = [MockToolDef(name="bad_tool", capability="unknown.capability")]
    # Provide schema to avoid secondary error
    mock_schemas = {"bad_tool": MockInput}

    errors = validate_registry(
        internal_tools=bad_internal, external_seeds=[], tool_input_models=mock_schemas
    )

    assert len(errors) >= 1
    assert any("bad_tool" in err and "unknown.capability" in err for err in errors)


def test_agent_scope_unknown_capability():
    """Should detect agent scope with unknown capability."""
    bad_scopes = {"test_agent": {"unknown.capability"}}
    errors = validate_registry(agent_scopes=bad_scopes)

    assert len(errors) >= 1
    assert any("test_agent" in err and "unknown.capability" in err for err in errors)


def test_critical_without_approval():
    """Should detect critical tool without approval."""
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class MockToolDef:
        name: str
        capability: str
        risk_level: str = "low"
        requires_approval: bool = False
        read_only: bool = False

    @dataclass(frozen=True, slots=True)
    class MockExternalSeed:
        name: str
        capability: str
        server: str
        risk_level: str = "medium"
        requires_approval: bool = True
        verified: bool = False

    # Mock capability catalog that has our test capability
    mock_catalog = {"test.capability": object()}

    bad_internal = [
        MockToolDef(
            name="critical_tool",
            capability="test.capability",
            risk_level="critical",
            requires_approval=False,
        )
    ]

    bad_external = [
        MockExternalSeed(
            name="external_critical",
            capability="test.capability",
            server="test",
            risk_level="critical",
            requires_approval=False,
        )
    ]

    errors = validate_registry(
        internal_tools=bad_internal,
        external_seeds=bad_external,
        capability_catalog=mock_catalog,
    )

    assert len(errors) >= 2
    assert any("critical_tool" in err and "does not require approval" in err for err in errors)
    assert any("external_critical" in err and "does not require approval" in err for err in errors)


def test_high_risk_without_approval_flagged():
    """High-risk tools without approval should be flagged (TOOL-P1-3).

    Critical-only was dead (no tool is ever critical); the check now covers 'high'.
    """
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class MockToolDef:
        name: str
        capability: str
        risk_level: str = "low"
        requires_approval: bool = False
        read_only: bool = False

    @dataclass(frozen=True, slots=True)
    class MockExternalSeed:
        name: str
        capability: str
        server: str
        risk_level: str = "medium"
        requires_approval: bool = True
        verified: bool = False

    mock_catalog = {"test.capability": object()}

    internal = [
        MockToolDef(
            name="high_internal",
            capability="test.capability",
            risk_level="high",
            requires_approval=False,
        )
    ]
    external = [
        MockExternalSeed(
            name="high_external",
            capability="test.capability",
            server="test",
            risk_level="high",
            requires_approval=False,
        )
    ]

    errors = validate_registry(
        internal_tools=internal,
        external_seeds=external,
        capability_catalog=mock_catalog,
    )

    assert any("high_internal" in err and "does not require approval" in err for err in errors)
    assert any("high_external" in err and "does not require approval" in err for err in errors)


def test_medium_risk_without_approval_not_flagged():
    """Medium-risk tools without approval are allowed by design (e.g. browser_* actions)."""
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class MockToolDef:
        name: str
        capability: str
        risk_level: str = "low"
        requires_approval: bool = False
        read_only: bool = False

    mock_catalog = {"test.capability": object()}

    class MockInput(BaseModel):
        test: str = Field(description="test")

    internal = [
        MockToolDef(
            name="browser_click_like",
            capability="test.capability",
            risk_level="medium",
            requires_approval=False,
            read_only=False,
        )
    ]

    errors = validate_registry(
        internal_tools=internal,
        external_seeds=[],
        capability_catalog=mock_catalog,
        tool_input_models={"browser_click_like": MockInput},
    )

    assert not any("browser_click_like" in err for err in errors)


def test_internal_tool_missing_schema():
    """Should detect internal tool without schema."""
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class MockToolDef:
        name: str
        capability: str
        risk_level: str = "low"
        requires_approval: bool = False
        read_only: bool = False

    # Mock capability catalog
    mock_catalog = {"test.capability": object()}

    bad_internal = [
        MockToolDef(name="missing_schema_tool", capability="test.capability", read_only=False)
    ]

    # Empty schema registry
    empty_schemas = {}

    errors = validate_registry(
        internal_tools=bad_internal,
        capability_catalog=mock_catalog,
        tool_input_models=empty_schemas,
    )

    assert len(errors) >= 1
    assert any("missing_schema_tool" in err and "TOOL_INPUT_MODELS" in err for err in errors)


def test_readonly_high_risk():
    """Should detect read-only tool with high risk or requiring approval."""
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class MockToolDef:
        name: str
        capability: str
        risk_level: str = "low"
        requires_approval: bool = False
        read_only: bool = False

    # Mock capability catalog
    mock_catalog = {"test.capability": object()}

    # Mock schema registry
    class MockInput(BaseModel):
        test: str = Field(description="test")

    mock_schemas = {"readonly_high_risk": MockInput, "readonly_approval": MockInput}

    bad_internal = [
        MockToolDef(
            name="readonly_high_risk",
            capability="test.capability",
            risk_level="high",
            requires_approval=False,
            read_only=True,
        ),
        MockToolDef(
            name="readonly_approval",
            capability="test.capability",
            risk_level="low",
            requires_approval=True,
            read_only=True,
        ),
    ]

    errors = validate_registry(
        internal_tools=bad_internal,
        capability_catalog=mock_catalog,
        tool_input_models=mock_schemas,
    )

    assert len(errors) >= 2
    assert any("readonly_high_risk" in err and "risk_level='high'" in err for err in errors)
    assert any("readonly_approval" in err and "requires approval" in err for err in errors)


def test_duplicate_tool_name_flagged():
    """Check 7: a name shared across internal + external catalogs is flagged (TOOL-P2-3)."""
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class MockToolDef:
        name: str
        capability: str
        risk_level: str = "low"
        requires_approval: bool = False
        read_only: bool = True

    @dataclass(frozen=True, slots=True)
    class MockExternalSeed:
        name: str
        capability: str
        server: str
        risk_level: str = "low"
        requires_approval: bool = False
        verified: bool = False

    class MockInput(BaseModel):
        test: str = Field(default="", description="t")

    mock_catalog = {"test.capability": object()}

    internal = [MockToolDef(name="shared_name", capability="test.capability")]
    external = [MockExternalSeed(name="shared_name", capability="test.capability", server="x")]

    errors = validate_registry(
        internal_tools=internal,
        external_seeds=external,
        capability_catalog=mock_catalog,
        tool_input_models={"shared_name": MockInput},
    )

    assert any("shared_name" in err and "Duplicate tool name" in err for err in errors)


def test_skip_validation_setting():
    """Should have skip_registry_validation setting with False default."""
    from src.config.settings import Settings

    settings = Settings()
    assert hasattr(settings, "skip_registry_validation")
    assert settings.skip_registry_validation is False
