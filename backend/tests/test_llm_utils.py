"""Tests for src.llm_utils.parse_llm_json."""

import json

import pytest

from src.llm_utils import coerce_to_object, parse_llm_json


class TestCoerceToObject:
    """Guarantee a dict for object-expecting extraction callers."""

    def test_dict_passthrough(self):
        assert coerce_to_object({"entities": [1]}, list_key="entities") == {"entities": [1]}

    def test_bare_list_is_wrapped(self):
        # The shape that crashed world_model.extract_from_event with .get() on a list.
        assert coerce_to_object([{"x": 1}], list_key="entities") == {"entities": [{"x": 1}]}

    def test_non_dict_non_list_becomes_empty(self):
        assert coerce_to_object("nope", list_key="memories") == {"memories": []}
        assert coerce_to_object(None, list_key="preferences") == {"preferences": []}


class TestParseLlmJson:
    """Core parsing: raw JSON, code-fenced JSON, whitespace."""

    def test_raw_json_object(self):
        assert parse_llm_json('{"key": "value"}') == {"key": "value"}

    def test_raw_json_array(self):
        assert parse_llm_json("[1, 2, 3]") == [1, 2, 3]

    def test_code_fenced_json(self):
        text = '```json\n{"entities": []}\n```'
        assert parse_llm_json(text) == {"entities": []}

    def test_code_fenced_no_lang_tag(self):
        text = '```\n{"a": 1}\n```'
        assert parse_llm_json(text) == {"a": 1}

    def test_whitespace_around_json(self):
        assert parse_llm_json('  \n {"x": 1} \n ') == {"x": 1}


class TestParseLlmJsonTrailingAndLeadingProse:
    """Robustness: JSON value surrounded by explanatory prose."""

    def test_json_object_then_trailing_prose(self):
        # The exact failing case: valid JSON object followed by a newline and
        # explanatory text (json.loads would raise "Extra data").
        text = '{"relevance_score": 0.8, "urgency": "high"}\n\nHere is why I scored it.'
        assert parse_llm_json(text) == {"relevance_score": 0.8, "urgency": "high"}

    def test_json_array_then_trailing_prose(self):
        text = "[1, 2, 3]\nThe array above lists the ids."
        assert parse_llm_json(text) == [1, 2, 3]

    def test_leading_prose_then_json_object(self):
        text = 'Here is the result:\n{"a": 1}'
        assert parse_llm_json(text) == {"a": 1}

    def test_leading_and_trailing_prose(self):
        text = 'Sure! Here you go:\n{"a": 1, "b": 2}\nLet me know if you need more.'
        assert parse_llm_json(text) == {"a": 1, "b": 2}

    def test_fenced_json_with_trailing_prose_after_fence(self):
        text = '```json\n{"ok": true}\n```\nThat is the JSON you asked for.'
        assert parse_llm_json(text) == {"ok": True}

    def test_leading_prose_with_stray_brace_before_json(self):
        # The first ``{`` belongs to bogus inline prose, not the real JSON. The
        # parser must scan forward to the next ``{`` candidate that actually
        # raw_decodes, instead of giving up on the first one.
        text = 'Here is {the answer}: {"relevance_score": 0.9}'
        assert parse_llm_json(text) == {"relevance_score": 0.9}

    def test_leading_prose_with_stray_brace_returns_default_when_all_fail(self):
        # No candidate decodes → default is returned (preserves old behavior).
        text = "Here is {the answer}: and nothing valid {at all"
        assert parse_llm_json(text, default={"fallback": True}) == {"fallback": True}

    def test_stray_bracket_before_real_array(self):
        text = "List [some prose] then the real one: [1, 2, 3]"
        assert parse_llm_json(text) == [1, 2, 3]


class TestParseLlmJsonDefault:
    """Default parameter: graceful fallback for empty/non-JSON responses."""

    def test_empty_string_with_default(self):
        result = parse_llm_json("", default={"entities": [], "relationships": []})
        assert result == {"entities": [], "relationships": []}

    def test_whitespace_only_with_default(self):
        result = parse_llm_json("   \n  ", default=[])
        assert result == []

    def test_non_json_text_with_default(self):
        result = parse_llm_json(
            "I couldn't extract any entities from this text.",
            default={"entities": []},
        )
        assert result == {"entities": []}

    def test_empty_string_without_default_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json("")

    def test_non_json_without_default_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_llm_json("not json at all")

    def test_valid_json_ignores_default(self):
        result = parse_llm_json('{"real": true}', default={"fallback": True})
        assert result == {"real": True}
