"""Manage locally-spawned HTTP MCP server processes, on demand.

Some MCP servers have no hosted remote endpoint and cannot run over stdio in a
multi-user, server-side context (e.g. workspace-mcp, whose stdio mode needs
interactive browser OAuth + on-disk creds). We run them as local host
subprocesses in their stateless OAuth21 HTTP mode, spawned on first use and
torn down when the last in-flight session releases them — reference-counted so
overlapping turns don't restart the process.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 30.0
READY_POLL_INTERVAL = 0.5
TERMINATE_GRACE_SECONDS = 5.0


@dataclass
class LocalServerSpec:
    """How to launch a local HTTP MCP server."""

    server_name: str
    argv: list[str]
    env: dict[str, str]
    path: str = "/mcp"
    port_env_var: str = "WORKSPACE_MCP_PORT"


@dataclass
class _Running:
    proc: Any
    port: int
    refcount: int = 0


@dataclass
class LocalMCPProcessManager:
    """Reference-counted lifecycle manager for local HTTP MCP processes."""

    specs: dict[str, LocalServerSpec]
    _running: dict[str, _Running] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def refcount(self, server_name: str) -> int:
        r = self._running.get(server_name)
        return r.refcount if r else 0

    async def ensure_running(self, server_name: str) -> str:
        """Start the server if needed, bump refcount, return its base MCP URL."""
        spec = self.specs[server_name]
        async with self._lock:
            running = self._running.get(server_name)
            if running is None or running.proc.returncode is not None:
                proc, port = await self._spawn(spec)
                running = _Running(proc=proc, port=port)
                self._running[server_name] = running
                try:
                    await self._wait_ready(port, spec.path)
                except Exception:
                    await self._stop(server_name)
                    raise
            running.refcount += 1
            return f"http://127.0.0.1:{running.port}{spec.path}"

    async def release(self, server_name: str) -> None:
        """Drop one reference; stop the process when the last is released."""
        async with self._lock:
            running = self._running.get(server_name)
            if not running:
                return
            running.refcount = max(0, running.refcount - 1)
            if running.refcount == 0:
                await self._stop(server_name)

    async def shutdown(self) -> None:
        """Force-stop all managed processes (called on app shutdown)."""
        async with self._lock:
            for server_name in list(self._running):
                await self._stop(server_name)

    # --- internals (patched in tests) ---

    async def _spawn(self, spec: LocalServerSpec) -> tuple[Any, int]:
        import os

        port = _free_port()
        env = {**spec.env, spec.port_env_var: str(port)}
        full_env = {**os.environ, **env}
        proc = await asyncio.create_subprocess_exec(
            *spec.argv,
            env=full_env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("[mcp:local] spawned %s pid=%s port=%d", spec.server_name, proc.pid, port)
        return proc, port

    async def _wait_ready(self, port: int, path: str) -> None:
        deadline = asyncio.get_event_loop().time() + READY_TIMEOUT_SECONDS
        url = f"http://127.0.0.1:{port}{path}"
        async with httpx.AsyncClient(timeout=2.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    await client.get(url)
                    return
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    await asyncio.sleep(READY_POLL_INTERVAL)
        raise TimeoutError(f"MCP server on port {port} not ready in {READY_TIMEOUT_SECONDS}s")

    async def _stop(self, server_name: str) -> None:
        running = self._running.pop(server_name, None)
        if not running:
            return
        proc = running.proc
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=TERMINATE_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
        logger.info("[mcp:local] stopped %s", server_name)


_manager: LocalMCPProcessManager | None = None


def get_local_process_manager() -> LocalMCPProcessManager | None:
    return _manager


def set_local_process_manager(manager: LocalMCPProcessManager | None) -> None:
    global _manager
    _manager = manager


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
