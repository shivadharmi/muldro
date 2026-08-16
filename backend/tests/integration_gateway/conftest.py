"""Skip the whole integration_gateway suite unless the live stack is reachable.

The stack (OpenConnector + adapter) is brought up out-of-band via
`infra/gateway/docker-compose.integration.yml`. These tests probe the adapter's
MCP port; if it's not reachable, the suite self-skips (like the real-DB tests).
"""

import socket

import pytest

_ADAPTER_HOST = "127.0.0.1"
_ADAPTER_PORT = 8100


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config, items):
    if _port_open(_ADAPTER_HOST, _ADAPTER_PORT):
        return
    skip = pytest.mark.skip(reason="gateway integration stack not running on :8100")
    for item in items:
        if "integration_gateway" in str(item.fspath):
            item.add_marker(skip)
