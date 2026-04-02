"""Tests for store_memory and store_preference internal MCP tools."""

from __future__ import annotations

import pytest


class TestStoreMemorySchema:
    def test_store_memory_in_catalog(self):
        from src.tools.catalog import INTERNAL_TOOLS

        names = {t.name for t in INTERNAL_TOOLS}
        assert "store_memory" in names

    def test_store_memory_capability(self):
        from src.tools.catalog import INTERNAL_TOOLS

        tool = next(t for t in INTERNAL_TOOLS if t.name == "store_memory")
        assert tool.capability == "internal.store_memory"
        assert tool.read_only is False
        assert tool.server == "intelligence"

    def test_store_preference_in_catalog(self):
        from src.tools.catalog import INTERNAL_TOOLS

        names = {t.name for t in INTERNAL_TOOLS}
        assert "store_preference" in names

    def test_store_preference_capability(self):
        from src.tools.catalog import INTERNAL_TOOLS

        tool = next(t for t in INTERNAL_TOOLS if t.name == "store_preference")
        assert tool.capability == "internal.store_preference"
        assert tool.read_only is False


class TestStoreMemoryInputModel:
    def test_defaults(self):
        from src.tools.schemas import StoreMemoryInput

        model = StoreMemoryInput(text="Test memory")
        assert model.text == "Test memory"
        assert model.memory_type == "fact"
        assert model.scope == "general"
        assert model.ttl_days == 0
        assert model.entity_ids == ""
        assert model.source == "agent"

    def test_custom_values(self):
        from src.tools.schemas import StoreMemoryInput

        model = StoreMemoryInput(
            text="A goal",
            memory_type="goal",
            scope="planning",
            ttl_days=30,
            entity_ids="ent_1,ent_2",
            source="user",
        )
        assert model.memory_type == "goal"
        assert model.scope == "planning"
        assert model.ttl_days == 30
        assert model.entity_ids == "ent_1,ent_2"
        assert model.source == "user"

    def test_ttl_days_non_negative(self):
        from pydantic import ValidationError

        from src.tools.schemas import StoreMemoryInput

        with pytest.raises(ValidationError):
            StoreMemoryInput(text="test", ttl_days=-1)


class TestStorePreferenceInputModel:
    def test_defaults(self):
        from src.tools.schemas import StorePreferenceInput

        model = StorePreferenceInput(text="Prefers morning meetings")
        assert model.text == "Prefers morning meetings"
        assert model.confidence == 0.5
        assert model.source_text == ""

    def test_confidence_bounds(self):
        from pydantic import ValidationError

        from src.tools.schemas import StorePreferenceInput

        with pytest.raises(ValidationError):
            StorePreferenceInput(text="test", confidence=1.5)
        with pytest.raises(ValidationError):
            StorePreferenceInput(text="test", confidence=-0.1)

    def test_valid_confidence(self):
        from src.tools.schemas import StorePreferenceInput

        model = StorePreferenceInput(text="test", confidence=0.9)
        assert model.confidence == 0.9


class TestLibrarianHasStoreMemory:
    def test_librarian_has_store_memory(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["librarian"]
        assert "internal.store_memory" in scope

    def test_librarian_retains_existing(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["librarian"]
        assert "internal.update_entity" in scope
        assert "internal.search" in scope


class TestPersonaHasStorePreference:
    def test_persona_has_store_preference(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["persona"]
        assert "internal.store_preference" in scope

    def test_persona_retains_existing(self):
        from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

        scope = AGENT_CAPABILITY_SCOPES["persona"]
        assert "internal.search" in scope
        assert "internal.extract_preferences" in scope


class TestCapabilityCatalog:
    def test_store_memory_capability_exists(self):
        from src.integrations.capabilities import CAPABILITY_CATALOG

        assert "internal.store_memory" in CAPABILITY_CATALOG

    def test_store_preference_capability_exists(self):
        from src.integrations.capabilities import CAPABILITY_CATALOG

        assert "internal.store_preference" in CAPABILITY_CATALOG

    def test_store_memory_not_read_only(self):
        from src.integrations.capabilities import CAPABILITY_CATALOG

        meta = CAPABILITY_CATALOG["internal.store_memory"]
        assert meta.read_only is False

    def test_store_preference_not_read_only(self):
        from src.integrations.capabilities import CAPABILITY_CATALOG

        meta = CAPABILITY_CATALOG["internal.store_preference"]
        assert meta.read_only is False


class TestToolInputModelsRegistry:
    def test_store_memory_in_registry(self):
        from src.tools.schemas import TOOL_INPUT_MODELS, StoreMemoryInput

        assert "store_memory" in TOOL_INPUT_MODELS
        assert TOOL_INPUT_MODELS["store_memory"] is StoreMemoryInput

    def test_store_preference_in_registry(self):
        from src.tools.schemas import TOOL_INPUT_MODELS, StorePreferenceInput

        assert "store_preference" in TOOL_INPUT_MODELS
        assert TOOL_INPUT_MODELS["store_preference"] is StorePreferenceInput
