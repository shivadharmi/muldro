from unittest.mock import patch

from src.integrations.runtime_preflight import check_mcp_runtimes


def test_reports_missing_runtimes():
    with patch("src.integrations.runtime_preflight.shutil.which", return_value=None):
        missing = check_mcp_runtimes(["uvx", "npx"])
    assert missing == ["uvx", "npx"]


def test_reports_present_runtimes():
    with patch("src.integrations.runtime_preflight.shutil.which", return_value="/usr/bin/x"):
        missing = check_mcp_runtimes(["uvx", "npx"])
    assert missing == []
