from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.local_process_manager import (
    LocalMCPProcessManager,
    LocalServerSpec,
)


def _spec() -> LocalServerSpec:
    return LocalServerSpec(
        server_name="google-workspace",
        argv=["uvx", "workspace-mcp", "--transport", "streamable-http"],
        env={"MCP_ENABLE_OAUTH21": "true"},
        path="/mcp",
    )


async def test_ensure_running_starts_once_and_refcounts():
    mgr = LocalMCPProcessManager({"google-workspace": _spec()})
    fake_proc = MagicMock()
    fake_proc.returncode = None

    with (
        patch.object(mgr, "_spawn", AsyncMock(return_value=(fake_proc, 51234))) as spawn,
        patch.object(mgr, "_wait_ready", AsyncMock(return_value=None)),
    ):
        url1 = await mgr.ensure_running("google-workspace")
        url2 = await mgr.ensure_running("google-workspace")

    assert url1 == "http://127.0.0.1:51234/mcp"
    assert url2 == url1
    spawn.assert_awaited_once()
    assert mgr.refcount("google-workspace") == 2


async def test_release_stops_when_refcount_hits_zero():
    mgr = LocalMCPProcessManager({"google-workspace": _spec()})
    fake_proc = MagicMock()
    fake_proc.returncode = None
    fake_proc.terminate = MagicMock()
    fake_proc.wait = AsyncMock(return_value=0)

    with (
        patch.object(mgr, "_spawn", AsyncMock(return_value=(fake_proc, 51234))),
        patch.object(mgr, "_wait_ready", AsyncMock(return_value=None)),
    ):
        await mgr.ensure_running("google-workspace")
        await mgr.ensure_running("google-workspace")

    await mgr.release("google-workspace")
    assert mgr.refcount("google-workspace") == 1
    fake_proc.terminate.assert_not_called()

    await mgr.release("google-workspace")
    assert mgr.refcount("google-workspace") == 0
    fake_proc.terminate.assert_called_once()


async def test_unknown_server_raises():
    mgr = LocalMCPProcessManager({})
    with pytest.raises(KeyError):
        await mgr.ensure_running("nope")


async def test_wait_ready_failure_stops_process_and_leaves_no_refcount():
    mgr = LocalMCPProcessManager({"google-workspace": _spec()})
    fake_proc = MagicMock()
    fake_proc.returncode = None
    fake_proc.terminate = MagicMock()
    fake_proc.wait = AsyncMock(return_value=0)

    with (
        patch.object(mgr, "_spawn", AsyncMock(return_value=(fake_proc, 51234))),
        patch.object(mgr, "_wait_ready", AsyncMock(side_effect=TimeoutError("boom"))),
    ):
        with pytest.raises(TimeoutError):
            await mgr.ensure_running("google-workspace")

    # No leaked refcount and the process was stopped.
    assert mgr.refcount("google-workspace") == 0
    fake_proc.terminate.assert_called_once()
