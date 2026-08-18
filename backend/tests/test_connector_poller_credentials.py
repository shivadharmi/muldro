"""Credential discriminator tests for ``ConnectorPoller.poll``.

Where a source's credential lives is a REGISTRY question
(``gateway_provider_for_source``), not a hardcoded map.

Three outcomes, exhaustive:

  None       + any              -> OAuthManager token (unchanged native path)
  provider   + GatewayConnector -> GatewayToolCaller, no OAuthManager
  provider   + native connector -> NON-permanent skip (the github deferral)

These tests must supply REAL classes: the discriminator calls
``issubclass(connector_cls, GatewayConnector)``, which raises TypeError on a
MagicMock. That is why this module does not reuse the MagicMock registry idiom
the poll() tests in ``test_poll_result.py`` use.

Split out of ``test_poll_result.py`` (file-size cap, engineering-standards §1).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, make_mock_settings


# ---------------------------------------------------------------------------
# The registry answer and the class hierarchy must AGREE — both directions.
#
# The negative half alone is not enough: if GmailConnector ever stopped
# subclassing GatewayConnector, gateway_provider_for_source("gmail") would still
# answer "gmail", the issubclass guard would fail, and gmail would silently take
# the DEFERRAL branch — an INFO log and a transient skip, no error surfaced, no
# permanent circuit, every test still green. That is exactly how gmail
# perception died in increment 2.
# ---------------------------------------------------------------------------
def test_ported_sources_really_are_gateway_connectors():
    """The positive half: gmail + calendar must BE GatewayConnector subclasses."""
    from src.connectors.base import CONNECTOR_REGISTRY
    from src.connectors.gateway_connector import GatewayConnector
    from src.integrations.gateway_actions import gateway_provider_for_source

    for source in ("gmail", "calendar"):
        assert gateway_provider_for_source(source) is not None, (
            f"{source} must be registry-claimed as gateway-backed"
        )
        assert issubclass(CONNECTOR_REGISTRY[source], GatewayConnector), (
            f"{source} is gateway-backed but {CONNECTOR_REGISTRY[source].__name__} is not a "
            "GatewayConnector — poll() would silently take the deferral branch and "
            f"{source} perception would go dark with no error"
        )


def test_deferred_and_native_sources_are_not_gateway_connectors():
    """The negative half: github (deferred) and slack (native) must NOT be."""
    from src.connectors.base import CONNECTOR_REGISTRY
    from src.connectors.gateway_connector import GatewayConnector
    from src.integrations.gateway_actions import gateway_provider_for_source

    assert gateway_provider_for_source("github") is not None
    assert not issubclass(CONNECTOR_REGISTRY["github"], GatewayConnector)

    assert gateway_provider_for_source("slack") is None
    assert not issubclass(CONNECTOR_REGISTRY["slack"], GatewayConnector)


class TestCredentialDiscriminator:
    @staticmethod
    def _poller():
        from src.orchestrator.connector_poller import ConnectorPoller

        poller = MagicMock(spec=ConnectorPoller)
        poller._settings = make_mock_settings()
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.first = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        poller._db_factory = MagicMock(return_value=mock_db)
        return poller

    async def test_gateway_source_never_constructs_oauth_manager(self):
        """A gateway source's credential lives in OpenConnector, not OAuthManager."""
        from src.connectors.gateway_connector import GatewayConnector
        from src.connectors.poll_result import PollResult
        from src.orchestrator.connector_poller import ConnectorPoller

        recorded = {}

        class _Ported(GatewayConnector):
            provider = "gmail"
            cursor_type = "timestamp"
            READ_ACTION = "gmail.get_profile"

            def __init__(self, settings=None, caller=None):
                super().__init__(settings=settings, caller=caller)
                recorded["caller"] = caller

            async def poll(self, user_id, cursor, credentials):
                recorded["credentials"] = credentials
                return PollResult(events=[], cursor="1755400000", error_class="none")

        def _explode(*args, **kwargs):
            raise AssertionError("OAuthManager was constructed for a gateway source")

        with patch("src.connectors.base.CONNECTOR_REGISTRY", {"gmail": _Ported}):
            with patch("src.services.oauth_manager.OAuthManager", _explode):
                events, cursor, error, cursor_type = await ConnectorPoller.poll(
                    self._poller(), "gmail", TEST_USER_ID, "ws_test"
                )

        assert events == []
        assert error is None
        assert cursor == "1755400000"
        assert cursor_type == "timestamp"
        assert recorded["caller"] is not None
        assert recorded["caller"].user_id == TEST_USER_ID
        assert recorded["caller"].workspace_id == "ws_test"
        # No credential dict at all — NOT {"access_token": ""}. An empty string is
        # a silent falsy that a future reader would treat as "no token"; an absent
        # key fails loudly with KeyError. "{}" is never a substitute for "I could
        # not read this" — here there is genuinely nothing to read.
        assert recorded["credentials"] == {}

    async def test_gateway_backed_but_unported_connector_skips_non_permanently(self):
        """github is gateway-backed but still a native connector (deferred port).

        Without the issubclass guard it would skip OAuthManager, read an empty
        access_token and return auth_failed — and auth_failed is PERMANENT,
        i.e. threshold 1. A source whose data path was deliberately not built
        must never open a permanent circuit.
        """
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.connectors.gateway_connector import GatewayConnector
        from src.integrations.gateway_actions import gateway_provider_for_source
        from src.orchestrator.connector_poller import ConnectorPoller
        from src.services.perception_policy import classify_error

        # Preconditions: github really is in this middle state.
        assert gateway_provider_for_source("github") is not None
        assert not issubclass(CONNECTOR_REGISTRY["github"], GatewayConnector)

        def _explode(*args, **kwargs):
            raise AssertionError("OAuthManager was constructed for a gateway source")

        with patch("src.services.oauth_manager.OAuthManager", _explode):
            events, cursor, error, _cursor_type = await ConnectorPoller.poll(
                self._poller(), "github", TEST_USER_ID, "ws_test"
            )

        assert events == []
        assert cursor is None
        assert error is not None
        assert "permanent" not in error.lower()
        assert "transient" in error.lower()
        assert classify_error(error) == "transient"

    async def test_native_source_still_takes_the_oauth_path(self):
        """A source the registry does not claim is still an OAuthManager question."""
        from src.connectors.base import BaseConnector, ConnectorHealth
        from src.connectors.poll_result import PollResult
        from src.integrations.gateway_actions import gateway_provider_for_source
        from src.orchestrator.connector_poller import ConnectorPoller
        from src.services.oauth_manager import TokenResult

        assert gateway_provider_for_source("slack") is None

        recorded = {}

        class _Native(BaseConnector):
            provider = "slack"
            cursor_type = "per_channel_ts"

            async def poll(self, user_id, cursor, credentials):
                recorded["credentials"] = credentials
                return PollResult(events=[], cursor="ts_1", error_class="none")

            def get_auth_url(self, scopes=None) -> str:  # pragma: no cover - unused
                return "https://example.invalid/auth"

            async def test(self, credentials) -> ConnectorHealth:  # pragma: no cover
                return ConnectorHealth(status="healthy")

        mock_oauth = AsyncMock()
        mock_oauth.get_valid_token_with_reason = AsyncMock(
            return_value=TokenResult(token="tok_native", reason="ok")
        )

        with patch("src.connectors.base.CONNECTOR_REGISTRY", {"slack": _Native}):
            with patch("src.services.oauth_manager.OAuthManager") as mock_oauth_cls:
                mock_oauth_cls.return_value = mock_oauth
                events, cursor, error, cursor_type = await ConnectorPoller.poll(
                    self._poller(), "slack", TEST_USER_ID, "ws_test"
                )

        assert events == []
        assert error is None
        assert cursor == "ts_1"
        assert cursor_type == "per_channel_ts"
        assert recorded["credentials"] == {"access_token": "tok_native"}
        assert mock_oauth.get_valid_token_with_reason.await_count == 1
        assert mock_oauth.get_valid_token_with_reason.await_args.args == (
            TEST_USER_ID,
            "slack",
        )


# ---------------------------------------------------------------------------
# The OAuth branch must resolve its provider through the canonical map, not the
# raw source name. Identity-mapped today for every non-gateway source, so this
# is a no-op guard — but the moment a second multi-source OAuth provider lands,
# the drop-gate (perception_tick -> provider_map.provider_for_source) would keep
# the source runnable while the poller looked it up under the source name, got
# no_token -> auth_failed -> PERMANENT, and opened the circuit after ONE attempt.
# ---------------------------------------------------------------------------
class TestOAuthProviderResolution:
    async def test_native_branch_asks_oauth_manager_for_the_canonical_provider(self):
        """A multi-source provider must be looked up by PROVIDER, not by source."""
        from src.connectors.base import BaseConnector, ConnectorHealth
        from src.connectors.poll_result import PollResult
        from src.orchestrator.connector_poller import ConnectorPoller
        from src.services.oauth_manager import TokenResult

        class _Native(BaseConnector):
            provider = "acme"
            cursor_type = "opaque"

            async def poll(self, user_id, cursor, credentials):
                return PollResult(events=[], cursor="c1", error_class="none")

            def get_auth_url(self, scopes=None) -> str:  # pragma: no cover - unused
                return "https://example.invalid/auth"

            async def test(self, credentials) -> ConnectorHealth:  # pragma: no cover
                return ConnectorHealth(status="healthy")

        mock_oauth = AsyncMock()
        mock_oauth.get_valid_token_with_reason = AsyncMock(
            return_value=TokenResult(token="tok", reason="ok")
        )

        with (
            patch("src.connectors.base.CONNECTOR_REGISTRY", {"acme_inbox": _Native}),
            patch(
                "src.integrations.provider_map._PROVIDER_SOURCES",
                {"acme": ["acme_inbox", "acme_tasks"]},
            ),
            patch("src.services.oauth_manager.OAuthManager") as mock_oauth_cls,
        ):
            mock_oauth_cls.return_value = mock_oauth
            _events, _cursor, error, _ct = await ConnectorPoller.poll(
                TestCredentialDiscriminator._poller(), "acme_inbox", TEST_USER_ID, "ws_test"
            )

        assert error is None
        assert mock_oauth.get_valid_token_with_reason.await_args.args == (
            TEST_USER_ID,
            "acme",
        ), "the native branch must resolve the provider via provider_map, not pass the source"
