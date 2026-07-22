"""The on-demand MCP subprocess's stderr is forwarded to the log, not discarded.

Previously stderr was ``DEVNULL``, so when a uvx/npx child died on its own
(package-resolve failure, auth error, traceback) there was no clue why — only a
downstream 'Session task completed unexpectedly'. Draining stderr to the logger
restores that visibility (and prevents a chatty child blocking on a full pipe).
"""

import logging
from unittest.mock import MagicMock, patch

from src.integrations import local_process_manager as lpm
from src.integrations.local_process_manager import _classify_stderr_level


class _FakeStream:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


async def test_drain_stderr_forwards_lines_to_logger():
    mgr = lpm.LocalMCPProcessManager(specs={})
    proc = MagicMock()
    proc.stderr = _FakeStream([b"uvx: failed to resolve package google-workspace-mcp\n"])

    with patch.object(lpm.logger, "warning") as warn:
        await mgr._drain_stderr("google-workspace", proc)

    warn.assert_called_once()
    args = warn.call_args.args
    assert args[1] == "google-workspace"
    assert "failed to resolve package" in args[2]


async def test_drain_stderr_handles_missing_stream():
    mgr = lpm.LocalMCPProcessManager(specs={})
    proc = MagicMock()
    proc.stderr = None
    # Must not raise when there is no stderr stream to drain.
    await mgr._drain_stderr("google-workspace", proc)


def test_classify_stderr_level_maps_by_content():
    """Routine subprocess chatter is INFO; danger signals elevate.

    The google-workspace MCP child writes all its operational logging to stderr
    (startup, auth, tool traces). Forwarding every line at WARNING buries real
    warnings. Level is inferred from content, defaulting to INFO.
    """
    # Normal FastMCP / uvicorn operational lines -> INFO
    assert _classify_stderr_level("[INFO] Transport: streamable-http") == logging.INFO
    assert _classify_stderr_level("INFO:     Application startup complete.") == logging.INFO
    assert _classify_stderr_level("[CALENDAR] Successfully retrieved 19 events") == logging.INFO
    assert _classify_stderr_level("[OAUTH] Successfully fetched user info") == logging.INFO
    assert _classify_stderr_level("[REGISTRY] Tool filtering: removed 1 tools") == logging.INFO

    # Self-described warnings stay WARNING
    assert (
        _classify_stderr_level("WARNING  Using non-secure cookies for development")
        == logging.WARNING
    )
    # Untagged crash reason (uvx resolve failure) must remain visible
    assert (
        _classify_stderr_level("uvx: failed to resolve package google-workspace-mcp")
        == logging.WARNING
    )

    # Tracebacks / errors elevate to ERROR
    assert _classify_stderr_level("Traceback (most recent call last):") == logging.ERROR
    assert _classify_stderr_level("google.auth.exceptions.RefreshError: bad token") == logging.ERROR


async def test_drain_stderr_normal_line_logged_at_info():
    """A routine INFO-tagged line is forwarded at INFO, not WARNING."""
    mgr = lpm.LocalMCPProcessManager(specs={})
    proc = MagicMock()
    proc.stderr = _FakeStream([b"[INFO] StreamableHTTP session manager started\n"])

    with (
        patch.object(lpm.logger, "info") as info,
        patch.object(lpm.logger, "warning") as warn,
    ):
        await mgr._drain_stderr("google-workspace", proc)

    info.assert_called_once()
    warn.assert_not_called()
    assert info.call_args.args[1] == "google-workspace"
    assert "session manager started" in info.call_args.args[2]
