"""Tests for Phase 5: Ontology Generalization."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestEntityTypes:
    def test_personal_entity_types_present(self):
        from src.services.world_model import ENTITY_TYPES

        personal_types = {
            "location",
            "health_record",
            "hobby",
            "family_member",
            "financial_account",
            "media_item",
            "recipe",
            "course",
            "contact_group",
        }
        for t in personal_types:
            assert t in ENTITY_TYPES, f"Missing personal entity type: {t}"

    def test_work_entity_types_still_present(self):
        from src.services.world_model import ENTITY_TYPES

        work_types = {
            "person",
            "organization",
            "project",
            "meeting",
            "goal",
            "task",
            "document",
            "repository",
        }
        for t in work_types:
            assert t in ENTITY_TYPES

    def test_unknown_type_falls_back_to_person(self):
        from src.services.world_model import ENTITY_TYPES

        raw_type = "alien_spacecraft"
        entity_type = raw_type if raw_type in ENTITY_TYPES else "person"
        assert entity_type == "person"

    def test_valid_personal_type_accepted(self):
        from src.services.world_model import ENTITY_TYPES

        raw_type = "hobby"
        entity_type = raw_type if raw_type in ENTITY_TYPES else "person"
        assert entity_type == "hobby"


class TestRelationTypes:
    def test_personal_relation_types_present(self):
        from src.services.world_model import RELATION_TYPES

        personal_rels = {
            "lives_at",
            "prescribed_by",
            "enrolled_in",
            "follows",
            "subscribes_to",
            "shares_with",
            "cares_for",
        }
        for r in personal_rels:
            assert r in RELATION_TYPES, f"Missing personal relation type: {r}"

    def test_work_relation_types_still_present(self):
        from src.services.world_model import RELATION_TYPES

        work_rels = {
            "works_on",
            "related_to",
            "reports_to",
            "owns",
            "member_of",
            "assigned_to",
        }
        for r in work_rels:
            assert r in RELATION_TYPES


class TestMemoryTypeLiterals:
    def test_memory_type_literal_values(self):
        # MemoryType is a Literal — verify its args
        import typing

        from src.api.schemas import MemoryType

        args = typing.get_args(MemoryType)
        assert "episodic" in args
        assert "semantic" in args
        assert "preference" in args
        assert "relationship" in args
        assert "task_context" in args

    def test_memory_scope_literal_values(self):
        import typing

        from src.api.schemas import MemoryScope

        args = typing.get_args(MemoryScope)
        assert set(args) == {"presentation", "planning", "general"}

    def test_briefing_style_literal_values(self):
        import typing

        from src.api.schemas import BriefingStyle

        args = typing.get_args(BriefingStyle)
        assert set(args) == {"founder", "personal", "academic", "general"}


class TestBriefingStylePrompts:
    def test_all_styles_have_prompts(self):
        from src.services.presenter import BRIEFING_STYLE_PROMPTS

        assert "founder" in BRIEFING_STYLE_PROMPTS
        assert "personal" in BRIEFING_STYLE_PROMPTS
        assert "academic" in BRIEFING_STYLE_PROMPTS
        assert "general" in BRIEFING_STYLE_PROMPTS

    def test_prompts_contain_json_schema(self):
        from src.services.presenter import BRIEFING_STYLE_PROMPTS

        for style, prompt in BRIEFING_STYLE_PROMPTS.items():
            assert '"headline"' in prompt, f"{style} prompt missing JSON schema"
            assert '"top_priorities"' in prompt, f"{style} prompt missing top_priorities"

    def test_default_is_general(self):
        from src.services.presenter import BRIEFING_STYLE_PROMPTS, BRIEFING_SYSTEM_PROMPT

        assert BRIEFING_SYSTEM_PROMPT == BRIEFING_STYLE_PROMPTS["general"]

    def test_founder_style_mentions_revenue(self):
        from src.services.presenter import BRIEFING_STYLE_PROMPTS

        assert "revenue" in BRIEFING_STYLE_PROMPTS["founder"].lower()

    def test_personal_style_mentions_family(self):
        from src.services.presenter import BRIEFING_STYLE_PROMPTS

        assert "family" in BRIEFING_STYLE_PROMPTS["personal"].lower()

    async def test_call_claude_uses_style(self):
        from src.services.presenter import BRIEFING_STYLE_PROMPTS, Presenter

        mock_settings = MagicMock()
        mock_settings.resolved_model = "claude-sonnet-4-6-20250514"
        mock_db = AsyncMock()

        json_text = (
            '{"headline":"test","top_priorities":[],"changes_since_last":[],'
            '"recommended_actions":[],"full_text":"test"}'
        )
        with (
            patch("src.services.presenter.get_anthropic_client", return_value=MagicMock()),
            patch(
                "src.services.presenter.complete_text",
                new=AsyncMock(return_value=json_text),
            ) as mock_complete,
        ):
            p = Presenter(mock_settings, mock_db)
            await p._call_claude("test context", style="founder")

        call_kwargs = mock_complete.call_args.kwargs
        assert call_kwargs["system"] == BRIEFING_STYLE_PROMPTS["founder"]


class TestSettingsDefaults:
    def test_briefing_style_default(self):
        from src.services.settings_service import SETTING_DEFAULTS

        assert ("presentation", "briefing_style") in SETTING_DEFAULTS
        assert SETTING_DEFAULTS[("presentation", "briefing_style")] == "general"

    def test_privacy_default(self):
        from src.services.settings_service import SETTING_DEFAULTS

        assert ("privacy", "auto_share_level") in SETTING_DEFAULTS
        assert SETTING_DEFAULTS[("privacy", "auto_share_level")] == "none"

    def test_autonomy_default(self):
        from src.services.settings_service import SETTING_DEFAULTS

        assert ("autonomy", "initiative_level") in SETTING_DEFAULTS
        assert SETTING_DEFAULTS[("autonomy", "initiative_level")] == "suggest"
