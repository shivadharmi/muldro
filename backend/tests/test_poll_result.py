"""Tests for typed PollResult connector failure propagation.

TDD — these tests are written BEFORE implementation and must pass after it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.conftest import TEST_USER_ID, make_mock_settings

# ---------------------------------------------------------------------------
# (a) + (b)  PollResult shape and connector failure typing
# ---------------------------------------------------------------------------


class TestPollResultType:
    """Unit tests for the PollResult dataclass itself."""

    def test_ok_result_defaults(self):
        from src.connectors.poll_result import PollResult

        r = PollResult(events=[], cursor="abc")
        assert r.error_class == "none"
        assert r.ok is True
        assert r.failed is False

    def test_failed_result(self):
        from src.connectors.poll_result import PollResult

        r = PollResult(events=[], cursor=None, error_class="transient")
        assert r.ok is False
        assert r.failed is True

    def test_frozen(self):
        """PollResult must be immutable."""
        import dataclasses

        from src.connectors.poll_result import PollResult

        r = PollResult(events=[], cursor="x")
        with pytest.raises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            r.cursor = "y"  # type: ignore[misc]

    def test_all_error_classes_accepted(self):
        from src.connectors.poll_result import PollResult

        for ec in ("none", "transient", "permanent", "rate_limited", "auth_failed"):
            r = PollResult(events=[], cursor=None, error_class=ec)  # type: ignore[arg-type]
            assert r.error_class == ec

    def test_ok_with_events(self):
        from src.connectors.poll_result import PollResult

        dummy_events = [object()]
        r = PollResult(events=dummy_events, cursor="cur")
        assert r.ok is True
        assert len(r.events) == 1


# ---------------------------------------------------------------------------
# (a) Connector returns failure-typed PollResult when HTTP raises
# ---------------------------------------------------------------------------


class TestGmailConnectorFailurePropagation:
    """Gmail connector poll() must return typed PollResult on failure."""

    @pytest.mark.asyncio
    async def test_exception_returns_transient_poll_result(self):
        """Network exception → PollResult with error_class='transient'."""
        from src.connectors.gmail import GmailConnector
        from src.connectors.poll_result import PollResult

        connector = GmailConnector(make_mock_settings())
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "cursor_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "transient"
        # Cursor must NOT advance on failure
        assert result.cursor == "cursor_abc"

    @pytest.mark.asyncio
    async def test_non_200_history_response_returns_failure(self):
        """HTTP 503 on history API → PollResult with error_class='transient'."""
        from src.connectors.gmail import GmailConnector
        from src.connectors.poll_result import PollResult

        connector = GmailConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "cursor_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "transient"
        assert result.cursor == "cursor_abc"

    @pytest.mark.asyncio
    async def test_401_returns_auth_failed(self):
        """HTTP 401 on history API → PollResult with error_class='auth_failed'."""
        from src.connectors.gmail import GmailConnector
        from src.connectors.poll_result import PollResult

        connector = GmailConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "cursor_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "auth_failed"
        assert result.cursor == "cursor_abc"

    @pytest.mark.asyncio
    async def test_429_returns_rate_limited(self):
        """HTTP 429 on history API → PollResult with error_class='rate_limited'."""
        from src.connectors.gmail import GmailConnector
        from src.connectors.poll_result import PollResult

        connector = GmailConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 429

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "cursor_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "rate_limited"
        assert result.cursor == "cursor_abc"

    @pytest.mark.asyncio
    async def test_empty_ok_poll_returns_ok_poll_result(self):
        """200 with no messages → PollResult ok with events=[] and advanced cursor."""
        from src.connectors.gmail import GmailConnector
        from src.connectors.poll_result import PollResult

        connector = GmailConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"historyId": "new_cursor_999", "history": []}

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "old_cursor", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.ok is True
        assert result.events == []
        # Cursor advances on success
        assert result.cursor == "new_cursor_999"


class TestSlackConnectorFailurePropagation:
    """Slack connector poll() must return typed PollResult on failure."""

    @pytest.mark.asyncio
    async def test_exception_returns_transient_poll_result(self):
        from src.connectors.poll_result import PollResult
        from src.connectors.slack_connector import SlackConnector

        connector = SlackConnector(make_mock_settings())
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "ts_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "transient"
        assert result.cursor == "ts_abc"

    @pytest.mark.asyncio
    async def test_non_200_returns_transient(self):
        from src.connectors.poll_result import PollResult
        from src.connectors.slack_connector import SlackConnector

        connector = SlackConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "ts_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.cursor == "ts_abc"

    @pytest.mark.parametrize(
        ("slack_error", "expected_class"),
        [
            ("ratelimited", "rate_limited"),
            ("token_revoked", "auth_failed"),
            ("invalid_auth", "auth_failed"),
            ("not_authed", "auth_failed"),
            ("account_inactive", "auth_failed"),
            ("internal_error", "transient"),
            ("fatal_error", "transient"),
            ("service_unavailable", "transient"),
            # Unknown error must fail-safe to transient, NEVER permanent —
            # permanent opens the circuit at threshold 1, killing a live source.
            ("weird_error", "transient"),
        ],
    )
    @pytest.mark.asyncio
    async def test_conversations_list_ok_false_error_classification(
        self, slack_error, expected_class
    ):
        """HTTP 200 + {"ok": false, "error": X} maps to the correct error_class."""
        from src.connectors.poll_result import PollResult
        from src.connectors.slack_connector import SlackConnector

        connector = SlackConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False, "error": slack_error}

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "ts_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == expected_class
        # Cursor must never advance on error.
        assert result.cursor == "ts_abc"

    @pytest.mark.asyncio
    async def test_slack_history_429_returns_rate_limited(self):
        """A 429 on conversations.history must surface as rate_limited, not silent success."""
        from src.connectors.poll_result import PollResult
        from src.connectors.slack_connector import SlackConnector

        connector = SlackConnector(make_mock_settings())

        # conversations.list succeeds with one channel.
        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {"ok": True, "channels": [{"id": "C1", "name": "general"}]}

        # conversations.history rate-limits (HTTP 429).
        hist_resp = MagicMock()
        hist_resp.status_code = 429

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=[list_resp, hist_resp])
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "ts_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "rate_limited"
        assert result.events == []
        # Cursor must NOT advance — prevents permanent message loss.
        assert result.cursor == "ts_abc"

    @pytest.mark.asyncio
    async def test_slack_history_okfalse_ratelimited_not_success(self):
        """HTTP 200 + {"ok": false, "error": "ratelimited"} must NOT be swallowed as success."""
        from src.connectors.poll_result import PollResult
        from src.connectors.slack_connector import SlackConnector

        connector = SlackConnector(make_mock_settings())

        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {"ok": True, "channels": [{"id": "C1", "name": "general"}]}

        hist_resp = MagicMock()
        hist_resp.status_code = 200
        hist_resp.json.return_value = {"ok": False, "error": "ratelimited"}

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=[list_resp, hist_resp])
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "ts_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "rate_limited"
        assert result.events == []
        assert result.cursor == "ts_abc"

    @pytest.mark.asyncio
    async def test_slack_history_okfalse_auth_returns_auth_failed(self):
        """HTTP 200 + {"ok": false, "error": "token_revoked"} maps to auth_failed."""
        from src.connectors.poll_result import PollResult
        from src.connectors.slack_connector import SlackConnector

        connector = SlackConnector(make_mock_settings())

        list_resp = MagicMock()
        list_resp.status_code = 200
        list_resp.json.return_value = {"ok": True, "channels": [{"id": "C1", "name": "general"}]}

        hist_resp = MagicMock()
        hist_resp.status_code = 200
        hist_resp.json.return_value = {"ok": False, "error": "token_revoked"}

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=[list_resp, hist_resp])
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "ts_abc", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "auth_failed"
        assert result.events == []
        assert result.cursor == "ts_abc"


class TestCalendarConnectorFailurePropagation:
    """Calendar connector poll() must return typed PollResult on failure."""

    @pytest.mark.asyncio
    async def test_exception_returns_transient_poll_result(self):
        from src.connectors.calendar import CalendarConnector
        from src.connectors.poll_result import PollResult

        connector = CalendarConnector(make_mock_settings())
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "sync_tok", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "transient"
        assert result.cursor == "sync_tok"

    @pytest.mark.asyncio
    async def test_401_returns_auth_failed(self):
        from src.connectors.calendar import CalendarConnector
        from src.connectors.poll_result import PollResult

        connector = CalendarConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await connector.poll(TEST_USER_ID, "sync_tok", {"access_token": "tok"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "auth_failed"
        assert result.cursor == "sync_tok"


class TestGitHubConnectorFailurePropagation:
    """GitHub connector poll() must return typed PollResult on failure."""

    @pytest.mark.asyncio
    async def test_exception_returns_transient_poll_result(self):
        from src.connectors.github_connector import GitHubConnector
        from src.connectors.poll_result import PollResult

        connector = GitHubConnector(make_mock_settings())
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))
            mock_cls.return_value = mock_client

            result = await connector.poll(
                TEST_USER_ID, "2026-01-01T00:00:00Z", {"access_token": "tok"}
            )

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "transient"
        assert result.cursor == "2026-01-01T00:00:00Z"

    async def _poll_403(self, headers: dict) -> object:
        """Drive github poll() against a mocked 403 response with the given headers."""
        from src.connectors.github_connector import GitHubConnector

        connector = GitHubConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        # headers must be a real dict so .get() returns None for absent keys
        # (a bare MagicMock.get() returns a truthy mock and would defeat discrimination).
        mock_resp.headers = headers

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            return await connector.poll(
                TEST_USER_ID, "2026-01-01T00:00:00Z", {"access_token": "tok"}
            )

    @pytest.mark.asyncio
    async def test_github_403_ratelimit_is_rate_limited(self):
        """403 + X-RateLimit-Remaining: 0 is a primary rate limit, not an auth failure."""
        from src.connectors.poll_result import PollResult

        result = await self._poll_403(
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"}
        )

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "rate_limited"
        assert result.cursor == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_github_403_secondary_ratelimit_is_rate_limited(self):
        """403 + Retry-After is a secondary/abuse rate limit, not an auth failure."""
        from src.connectors.poll_result import PollResult

        result = await self._poll_403({"Retry-After": "60"})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "rate_limited"
        assert result.cursor == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_github_403_no_ratelimit_headers_is_auth_failed(self):
        """403 with no rate-limit headers is a genuine auth/permission failure."""
        from src.connectors.poll_result import PollResult

        result = await self._poll_403({})

        assert isinstance(result, PollResult)
        assert result.failed is True
        assert result.error_class == "auth_failed"
        assert result.cursor == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# (c) + (d)  Scheduler routing: failure → record_failure, ok-empty → record_success
# ---------------------------------------------------------------------------


class TestPerceptionCycleRouting:
    """run_perception_cycle must route PollResult.failed → record_failure."""

    @pytest.mark.asyncio
    async def test_failed_poll_returns_error_status(self):
        """run_perception_cycle returns status=error when connector poll fails."""

        with patch(
            "src.connectors.base.CONNECTOR_REGISTRY",
            {"gmail": MagicMock()},
        ):
            from src.orchestrator.perception_runner import PerceptionRunner

            orchestrator = MagicMock(spec=PerceptionRunner)
            orchestrator._db_factory = AsyncMock()
            orchestrator._budget = MagicMock()
            orchestrator._budget.get_budget_status = AsyncMock(return_value=MagicMock())
            orchestrator._budget.should_allow_perception = MagicMock(return_value=True)
            orchestrator._trace_manager = MagicMock()
            orchestrator._trace_manager.start_trace = MagicMock(
                return_value=MagicMock(trace_id="t1")
            )
            orchestrator._trace_manager.finish_trace = AsyncMock()

            # Connector polling now lives on ConnectorPoller; run_perception_cycle
            # calls self._poller.poll(). Simulate a failed poll returning
            # ([], cursor, error_msg, type) — what PollResult.failed produces.
            orchestrator._poller = MagicMock()
            orchestrator._poller.poll = AsyncMock(
                return_value=([], "cursor_abc", "Poll failed: transient", "opaque")
            )

            result = await PerceptionRunner.run_perception_cycle(
                orchestrator, "gmail", TEST_USER_ID, "ws_test"
            )

            assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_poll_connector_transient_failure_sets_error(self):
        """ConnectorPoller.poll translates PollResult.failed into ([], cursor, error_msg, type)."""
        from src.connectors.poll_result import PollResult

        with patch("src.connectors.base.CONNECTOR_REGISTRY") as mock_registry:
            mock_connector_cls = MagicMock()
            mock_connector = AsyncMock()
            mock_connector.cursor_type = "opaque"
            mock_connector_cls.return_value = mock_connector
            mock_registry.get.return_value = mock_connector_cls

            # Connector returns a failed PollResult
            failed_result = PollResult(events=[], cursor="old_cursor", error_class="transient")
            mock_connector.poll = AsyncMock(return_value=failed_result)

            from src.orchestrator.connector_poller import ConnectorPoller

            poller = MagicMock(spec=ConnectorPoller)
            poller._settings = make_mock_settings()

            # Mock DB session context for cursor fetch
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.first = MagicMock(return_value=None)
            mock_db.execute = AsyncMock(return_value=mock_result)
            poller._db_factory = MagicMock(return_value=mock_db)

            # OAuthManager is a local import inside poll(); patch the module it lives in
            with patch("src.services.oauth_manager.OAuthManager") as mock_oauth_cls:
                mock_oauth = AsyncMock()
                mock_oauth.get_valid_token = AsyncMock(return_value="tok")
                mock_oauth_cls.return_value = mock_oauth

                (
                    events,
                    new_cursor,
                    poll_error,
                    cursor_type,
                ) = await ConnectorPoller.poll(poller, "gmail", TEST_USER_ID, "ws_test")

            assert events == []
            assert poll_error is not None
            assert "transient" in poll_error.lower()
            # Cursor must NOT advance on failure
            assert new_cursor == "old_cursor"

    @pytest.mark.asyncio
    async def test_poll_connector_ok_empty_no_error(self):
        """ConnectorPoller.poll with ok-empty PollResult returns no error and advances cursor."""
        from src.connectors.poll_result import PollResult

        with patch("src.connectors.base.CONNECTOR_REGISTRY") as mock_registry:
            mock_connector_cls = MagicMock()
            mock_connector = AsyncMock()
            mock_connector.cursor_type = "opaque"
            mock_connector_cls.return_value = mock_connector
            mock_registry.get.return_value = mock_connector_cls

            ok_empty_result = PollResult(events=[], cursor="new_cursor_789", error_class="none")
            mock_connector.poll = AsyncMock(return_value=ok_empty_result)

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

            # OAuthManager is a local import inside poll(); patch the module it lives in
            with patch("src.services.oauth_manager.OAuthManager") as mock_oauth_cls:
                mock_oauth = AsyncMock()
                mock_oauth.get_valid_token = AsyncMock(return_value="tok")
                mock_oauth_cls.return_value = mock_oauth

                (
                    events,
                    new_cursor,
                    poll_error,
                    cursor_type,
                ) = await ConnectorPoller.poll(poller, "gmail", TEST_USER_ID, "ws_test")

            assert events == []
            assert poll_error is None
            assert new_cursor == "new_cursor_789"


# ---------------------------------------------------------------------------
# (e) Error-class mapping reaches PerceptionPolicyService correctly
# ---------------------------------------------------------------------------


class TestErrorClassPropagation:
    """Error-class from PollResult maps to correct circuit-breaker behaviour."""

    def test_rate_limited_uses_transient_threshold(self):
        """rate_limited error_class → lower threshold (treated as transient)."""
        from src.connectors.poll_result import error_class_to_policy_error

        # rate_limited should map to something PerceptionPolicyService treats
        # as transient (i.e. higher failure threshold before opening circuit)
        error_msg = error_class_to_policy_error("rate_limited")
        assert "429" in error_msg or "rate" in error_msg.lower()

    def test_auth_failed_uses_permanent_threshold(self):
        """auth_failed error_class → permanent classification (opens circuit fast)."""
        from src.connectors.poll_result import error_class_to_policy_error
        from src.services.perception_policy import classify_error

        error_msg = error_class_to_policy_error("auth_failed")
        classified = classify_error(error_msg)
        assert classified == "permanent"

    def test_transient_maps_to_transient_classification(self):
        """transient error_class → transient classify_error result."""
        from src.connectors.poll_result import error_class_to_policy_error
        from src.services.perception_policy import classify_error

        error_msg = error_class_to_policy_error("transient")
        classified = classify_error(error_msg)
        assert classified == "transient"

    def test_permanent_uses_permanent_threshold(self):
        """permanent error_class → permanent classify_error result (opens circuit after 1 failure).

        Regression test: the sentinel produced by error_class_to_policy_error("permanent")
        must match _PERMANENT_PATTERNS so that unrecoverable 4xx errors open the
        circuit immediately (threshold=1) instead of falling through to unknown (threshold=3).
        """
        from src.connectors.poll_result import error_class_to_policy_error
        from src.services.perception_policy import classify_error

        error_msg = error_class_to_policy_error("permanent")
        classified = classify_error(error_msg)
        assert classified == "permanent"


# ---------------------------------------------------------------------------
# (f) Preflight / exception error paths must carry a classification keyword
#
# Regression for: "Circuit opened for .../gmail after 3 failures
# (error_class=unknown, threshold=3)" triggered by transient OAuth token-refresh
# blips. The preflight/exception branches of ConnectorPoller.poll() bypassed the
# typed-error pipeline and emitted prose with no classification keyword, so every
# one of them bucketed as unknown (threshold=3). They must instead classify so
# that:
#   - credential-acquisition failure  -> transient (threshold 6, tolerates blips)
#   - no connector registered         -> permanent (threshold 1, never self-heals)
#   - generic exception               -> classified, never bare unknown
# ---------------------------------------------------------------------------


async def _run_poll_with_token(token, *, source="gmail", reason=None):
    """Drive ConnectorPoller.poll with a stubbed connector + given OAuth token.

    The connector itself returns an ok-empty PollResult; the test controls
    whether the *preflight* token check fails by choosing ``token`` (and the
    ``reason`` the OAuth manager reports — defaults to ``ok`` when a token is
    present, ``no_token`` otherwise).
    """
    from src.connectors.poll_result import PollResult
    from src.orchestrator.connector_poller import ConnectorPoller
    from src.services.oauth_manager import TokenResult

    with patch("src.connectors.base.CONNECTOR_REGISTRY") as mock_registry:
        mock_connector_cls = MagicMock()
        mock_connector = AsyncMock()
        mock_connector.cursor_type = "opaque"
        mock_connector_cls.return_value = mock_connector
        mock_registry.get.return_value = mock_connector_cls
        mock_connector.poll = AsyncMock(
            return_value=PollResult(events=[], cursor="c", error_class="none")
        )

        poller = MagicMock(spec=ConnectorPoller)
        poller._settings = make_mock_settings()

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.first = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        poller._db_factory = MagicMock(return_value=mock_db)

        with patch("src.services.oauth_manager.OAuthManager") as mock_oauth_cls:
            _reason = reason or ("ok" if token is not None else "no_token")
            mock_oauth = AsyncMock()
            mock_oauth.get_valid_token = AsyncMock(return_value=token)
            mock_oauth.get_valid_token_with_reason = AsyncMock(
                return_value=TokenResult(token=token, reason=_reason)
            )
            mock_oauth_cls.return_value = mock_oauth

            return await ConnectorPoller.poll(poller, source, TEST_USER_ID, "ws_test")


class TestPreflightErrorClassification:
    @pytest.mark.asyncio
    async def test_no_token_classifies_permanent(self):
        """A confirmed "no usable credential" (never connected / revoked / no
        refresh token) must classify as PERMANENT (circuit opens immediately at
        threshold 1). Retrying forever can't conjure a credential — open fast and
        surface re-auth rather than churning. (The poller emits the auth_failed
        sentinel, which classify_error buckets permanent via its 401/unauthorized
        patterns.)
        """
        from src.services.perception_policy import classify_error

        for reason in ("no_token", "no_refresh_token", "revoked"):
            events, _new_cursor, poll_error, _ = await _run_poll_with_token(
                None, reason=reason
            )
            assert events == []
            assert poll_error is not None
            assert classify_error(poll_error) == "permanent", reason

    @pytest.mark.asyncio
    async def test_refresh_blip_classifies_transient(self):
        """A genuine token-refresh HTTP failure (network/5xx) is the one truly
        transient case — keep tolerating it (threshold 6) rather than opening the
        circuit on a momentary blip.
        """
        from src.services.perception_policy import classify_error

        events, _new_cursor, poll_error, _ = await _run_poll_with_token(
            None, reason="refresh_failed"
        )

        assert events == []
        assert poll_error is not None
        assert classify_error(poll_error) == "transient"

    @pytest.mark.asyncio
    async def test_no_connector_registered_classifies_permanent(self):
        """An unregistered source is a config error that never self-heals."""
        from src.orchestrator.connector_poller import ConnectorPoller
        from src.services.perception_policy import classify_error

        with patch("src.connectors.base.CONNECTOR_REGISTRY") as mock_registry:
            mock_registry.get.return_value = None

            poller = MagicMock(spec=ConnectorPoller)
            poller._settings = make_mock_settings()

            events, new_cursor, poll_error, _ = await ConnectorPoller.poll(
                poller, "nonexistent", TEST_USER_ID, "ws_test"
            )

        assert events == []
        assert poll_error is not None
        assert classify_error(poll_error) == "permanent"

    @pytest.mark.asyncio
    async def test_generic_exception_classifies_not_unknown(self):
        """A generic poll() exception must classify to a real class, not unknown."""
        from src.connectors.poll_result import PollResult  # noqa: F401
        from src.orchestrator.connector_poller import ConnectorPoller
        from src.services.perception_policy import classify_error

        with patch("src.connectors.base.CONNECTOR_REGISTRY") as mock_registry:
            mock_connector_cls = MagicMock()
            mock_connector = AsyncMock()
            mock_connector.cursor_type = "opaque"
            mock_connector_cls.return_value = mock_connector
            mock_registry.get.return_value = mock_connector_cls
            mock_connector.poll = AsyncMock(side_effect=RuntimeError("kaboom"))

            poller = MagicMock(spec=ConnectorPoller)
            poller._settings = make_mock_settings()

            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.first = MagicMock(return_value=None)
            mock_db.execute = AsyncMock(return_value=mock_result)
            poller._db_factory = MagicMock(return_value=mock_db)

            with patch("src.services.oauth_manager.OAuthManager") as mock_oauth_cls:
                mock_oauth = AsyncMock()
                mock_oauth.get_valid_token = AsyncMock(return_value="tok")
                mock_oauth_cls.return_value = mock_oauth

                events, _, poll_error, _ = await ConnectorPoller.poll(
                    poller, "gmail", TEST_USER_ID, "ws_test"
                )

        assert events == []
        assert poll_error is not None
        # A generic exception must not silently bucket as unknown/threshold-3.
        assert classify_error(poll_error) != "unknown"
