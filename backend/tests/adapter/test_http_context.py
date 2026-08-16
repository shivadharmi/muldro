from unittest.mock import patch

from src.adapter.http_context import bearer_token


def test_strips_bearer_prefix_case_insensitively():
    with patch(
        "src.adapter.http_context.get_http_headers",
        return_value={"authorization": "Bearer abc.def"},
    ):
        assert bearer_token() == "abc.def"


def test_returns_raw_when_no_bearer_prefix():
    with patch(
        "src.adapter.http_context.get_http_headers",
        return_value={"authorization": "abc.def"},
    ):
        assert bearer_token() == "abc.def"


def test_returns_empty_when_absent():
    with patch("src.adapter.http_context.get_http_headers", return_value={}):
        assert bearer_token() == ""
