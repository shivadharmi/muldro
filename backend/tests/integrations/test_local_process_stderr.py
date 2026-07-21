"""The on-demand MCP subprocess's stderr is forwarded to the log, not discarded.

Previously stderr was ``DEVNULL``, so when a uvx/npx child died on its own
(package-resolve failure, auth error, traceback) there was no clue why — only a
downstream 'Session task completed unexpectedly'. Draining stderr to the logger
restores that visibility (and prevents a chatty child blocking on a full pipe).
"""

from unittest.mock import MagicMock, patch

from src.integrations import local_process_manager as lpm


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
