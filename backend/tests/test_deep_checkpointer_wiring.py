"""Step 6A.5: JarvisOrchestrator threads a checkpointer_provider to AgentInvoker; it returns
the injected saver, or None (→ MemorySaver at the seam) by default.
"""

from unittest.mock import MagicMock, patch

from src.orchestrator.services import ServiceContainer
from tests.conftest import make_mock_settings


def _make_orchestrator(**kwargs):
    """Build a JarvisOrchestrator with minimal mocks (mirrors test_jarvis_conversation_embedding).

    Accepts extra kwargs (e.g. checkpointer_provider=...) that are forwarded to the
    JarvisOrchestrator constructor.
    """
    settings = make_mock_settings()
    settings.use_bedrock = False

    with patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        from src.orchestrator.jarvis import JarvisOrchestrator

        return JarvisOrchestrator(
            settings=settings,
            db_factory=MagicMock(),
            services=ServiceContainer(),
            **kwargs,
        )


def test_orchestrator_threads_checkpointer_provider():
    """A custom checkpointer_provider is threaded through to AgentInvoker._checkpointer_provider."""
    sentinel = object()
    orch = _make_orchestrator(checkpointer_provider=lambda: sentinel)
    assert orch._invoker._checkpointer_provider() is sentinel


def test_default_provider_returns_none():
    """Without a provider, _checkpointer_provider() returns None (→ MemorySaver fallback)."""
    orch = _make_orchestrator()
    assert orch._invoker._checkpointer_provider() is None
