"""Client-surfaced error sanitization in the services layer.

Every place a service writes an error into a field that is later serialized to
a client (execution surfaces, run/step error, A2UI payloads, public health
endpoint) must expose only the SAFE message + a stable error code (+ a
correlation id) — never the raw ``str(exc)``. Server-side logging of the raw
exception stays; these tests only assert the *surfaced* fields.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.graph_engine import GraphEngine
from src.services.graph_executor import _safe_error_fields
from src.services.notifier import Notifier
from src.services.vector_store import VectorStore
from tests.conftest import make_mock_settings

# A secret-looking internal string that must never reach a client field.
SECRET = "postgres://admin:hunter2@db.internal:5432/muldro"
GENERIC_MESSAGE = "Something went wrong. Please try again."


# ── graph_executor._safe_error_fields (run.error / step.error / output_data) ──


def test_safe_error_fields_hides_raw_exception():
    fields = _safe_error_fields(ValueError(f"connection refused to {SECRET}"))
    assert fields["message"] == GENERIC_MESSAGE
    assert fields["error_code"] == "internal_error"
    assert fields["correlation_id"]
    assert SECRET not in str(fields)
    assert "connection refused" not in str(fields)


def test_safe_error_fields_uses_domain_safe_message():
    from src.errors import ExternalServiceError

    fields = _safe_error_fields(ExternalServiceError(f"anthropic 529 from {SECRET}"))
    assert fields["error_code"] == "upstream_unavailable"
    assert SECRET not in str(fields)
    assert "anthropic" not in str(fields).lower()


# ── VectorStore.health (PUBLIC /v1/health/stores) ───────────────────────


@pytest.mark.asyncio
async def test_vector_store_health_sanitizes_error():
    settings = make_mock_settings(qdrant_url="http://qdrant:6333", qdrant_api_key="k")
    store = VectorStore(settings=settings)
    bad_client = MagicMock()
    bad_client.get_collections = AsyncMock(side_effect=RuntimeError(f"boom {SECRET}"))
    store._client = bad_client

    result = await store.health()

    assert result["status"] == "unreachable"
    assert result["error"] == GENERIC_MESSAGE
    assert result["error_code"] == "internal_error"
    assert SECRET not in str(result)


# ── GraphEngine.health (PUBLIC /v1/health/stores) ───────────────────────


@pytest.mark.asyncio
async def test_graph_engine_health_sanitizes_error():
    settings = make_mock_settings(neo4j_url="bolt://neo4j:7687")
    engine = GraphEngine(settings)

    async def _boom():
        raise RuntimeError(f"neo4j auth failed {SECRET}")

    engine._get_driver = _boom  # raises before session

    result = await engine.health()

    assert result["status"] == "unreachable"
    assert result["error"] == GENERIC_MESSAGE
    assert result["error_code"] == "internal_error"
    assert SECRET not in str(result)


# ── Notifier delivery error dicts (may be relayed to a surface) ──────────


@pytest.mark.asyncio
async def test_notifier_slack_delivery_error_sanitized():
    notifier = Notifier(surface_registry=MagicMock(), db=AsyncMock())

    # Force the MCP import path to raise with a secret-bearing exception.
    import src.connectors.mcp_bridge as bridge

    original = bridge.is_mcp_tool
    bridge.is_mcp_tool = MagicMock(side_effect=RuntimeError(f"slack token {SECRET}"))
    try:
        notification = MagicMock()
        notification.title = "t"
        notification.body = "b"
        notification.user_id = "user_1"
        notification.data = {}
        result = await notifier._deliver_slack(notification)
    finally:
        bridge.is_mcp_tool = original

    assert result["status"] == "error"
    assert result["error"] == GENERIC_MESSAGE
    assert result["error_code"] == "internal_error"
    assert SECRET not in str(result)
