"""Base connector interface for all data source connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from src.connectors.poll_result import PollResult
from src.services.event_processor import RawEvent


@dataclass
class ConnectorHealth:
    provider: str
    status: str  # healthy, degraded, down
    last_poll_at: datetime | None
    error: str | None = None
    events_last_poll: int = 0


class BaseConnector(ABC):
    """Abstract base class for all data source connectors."""

    provider: str
    cursor_type: str = "opaque"  # Override per connector: history_id, sync_token, etc.

    # Override in subclasses that support webhooks / write actions
    supports_webhooks: bool = False
    supports_actions: bool = False
    available_actions: list[str] = []

    def __init__(self, settings=None):
        self._settings = settings

    @abstractmethod
    async def poll(self, user_id: str, cursor: str | None, credentials: dict) -> PollResult:
        """Poll for new events since cursor. Returns a typed PollResult.

        Implementations MUST:
        - Return PollResult with error_class="none" on success (events may be empty).
        - Return PollResult with the appropriate error_class and the *unchanged*
          incoming cursor on any failure — never advance the cursor on error.
        """

    @abstractmethod
    async def test(self, credentials: dict) -> ConnectorHealth:
        """Test the connection and return health status."""

    @abstractmethod
    async def get_auth_url(self, scopes: list[str] | None = None) -> str:
        """Get the OAuth authorization URL for this connector."""

    async def handle_webhook(self, payload: dict) -> list[RawEvent]:
        """Parse incoming webhook payload into RawEvents.

        Override in connectors that set supports_webhooks = True.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support webhooks")

    async def execute_action(self, action: str, params: dict, credentials: dict) -> dict:
        """Execute a write action (e.g. send_email, create_issue).

        Override in connectors that set supports_actions = True.
        Returns a result dict with at least {"status": "ok"|"error"}.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support actions")


# Registry of connector classes by provider name
CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {}


def register_connector(provider: str):
    """Decorator to register a connector class."""

    def decorator(cls: type[BaseConnector]):
        CONNECTOR_REGISTRY[provider] = cls
        cls.provider = provider
        return cls

    return decorator
