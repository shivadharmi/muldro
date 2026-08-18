"""Base connector interface for all data source connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from src.connectors.poll_result import PollResult


@dataclass
class ConnectorHealth:
    provider: str
    status: str  # healthy, degraded, down
    last_poll_at: datetime | None
    error: str | None = None
    events_last_poll: int = 0


class BaseConnector(ABC):
    """Abstract base class for all data source connectors.

    Single-account assumption (multi-account is NOT supported):
        Every connector hardcodes ``source_account_id = "<source>_primary"`` and
        the dedup idempotency keys derived from emitted events omit the account
        id entirely. Cursor watermark keys are likewise per (user, source), not
        per account. As a result a user can connect only one account per source.

        Supporting multiple accounts per source per user would require threading
        an account id through three places consistently:
          1. the cursor/watermark key (so each account tracks its own position),
          2. ``RawEvent.source_account_id`` (currently the literal
             ``"<source>_primary"``), and
          3. the dedup idempotency key (so identical entity ids from different
             accounts do not collide / falsely dedup).
        Until all three are updated together, do not add a second account for a
        source — it would silently share cursors and collide on dedup.
    """

    provider: str
    cursor_type: str = "opaque"  # Override per connector: history_id, sync_token, etc.

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

    # NOTE: this class deliberately exposes no write-action or webhook-payload
    # hooks. Write actions go through MCP (see connectors/mcp_bridge.py), and
    # push notifications are wake signals only — the receiver sets pending_run
    # and the connector's normal poll fetches the data, so nothing ever needed
    # a payload-parsing hook here.


# Registry of connector classes by provider name
CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {}


def register_connector(provider: str):
    """Decorator to register a connector class."""

    def decorator(cls: type[BaseConnector]):
        CONNECTOR_REGISTRY[provider] = cls
        cls.provider = provider
        return cls

    return decorator
