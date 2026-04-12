"""Tests for src.llm_utils.parse_llm_json."""

import json

import pytest

from src.llm_utils import parse_llm_json


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
