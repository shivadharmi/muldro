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

    @pytest.mark.asyncio
    async def test_403_returns_auth_failed(self):
        from src.connectors.github_connector import GitHubConnector
        from src.connectors.poll_result import PollResult

        connector = GitHubConnector(make_mock_settings())
        mock_resp = MagicMock()
        mock_resp.status_code = 403

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await connector.poll(
                TEST_USER_ID, "2026-01-01T00:00:00Z", {"access_token": "tok"}
            )

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

        with (
            patch("src.orchestrator.jarvis.JarvisOrchestrator._poll_connector") as mock_poll,
            patch(
                "src.connectors.base.CONNECTOR_REGISTRY",
                {"gmail": MagicMock()},
            ),
        ):
            from src.orchestrator.jarvis import JarvisOrchestrator

            orchestrator = MagicMock(spec=JarvisOrchestrator)
            orchestrator._db_factory = AsyncMock()
            orchestrator._budget = MagicMock()
            orchestrator._budget.get_budget_status = AsyncMock(return_value=MagicMock())
            orchestrator._budget.should_allow_perception = MagicMock(return_value=True)
            orchestrator._trace_manager = MagicMock()
            orchestrator._trace_manager.start_trace = MagicMock(
                return_value=MagicMock(trace_id="t1")
            )
            orchestrator._trace_manager.finish_trace = AsyncMock()

            # Simulate _poll_connector returning ([], None, "Poll timed out", "opaque")
            # which is what happens when PollResult.failed is detected
            mock_poll.return_value = ([], "cursor_abc", "Poll failed: transient", "opaque")
            orchestrator._poll_connector = mock_poll

            result = await JarvisOrchestrator.run_perception_cycle(
                orchestrator, "gmail", TEST_USER_ID, "ws_test"
            )

            assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_poll_connector_transient_failure_sets_error(self):
        """_poll_connector translates PollResult.failed into ([], cursor, error_msg, type)."""
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

            from src.orchestrator.jarvis import JarvisOrchestrator

            orchestrator = MagicMock(spec=JarvisOrchestrator)
            orchestrator._settings = make_mock_settings()

            # Mock DB session context for cursor fetch
            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.first = MagicMock(return_value=None)
            mock_db.execute = AsyncMock(return_value=mock_result)
            orchestrator._db_factory = MagicMock(return_value=mock_db)

            # OAuthManager is a local import inside _poll_connector; patch the module it lives in
            with patch("src.services.oauth_manager.OAuthManager") as mock_oauth_cls:
                mock_oauth = AsyncMock()
                mock_oauth.get_valid_token = AsyncMock(return_value="tok")
                mock_oauth_cls.return_value = mock_oauth

                (
                    events,
                    new_cursor,
                    poll_error,
                    cursor_type,
                ) = await JarvisOrchestrator._poll_connector(
                    orchestrator, "gmail", TEST_USER_ID, "ws_test"
                )

            assert events == []
            assert poll_error is not None
            assert "transient" in poll_error.lower()
            # Cursor must NOT advance on failure
            assert new_cursor == "old_cursor"

    @pytest.mark.asyncio
    async def test_poll_connector_ok_empty_no_error(self):
        """_poll_connector with ok-empty PollResult returns no error and advances cursor."""
        from src.connectors.poll_result import PollResult

        with patch("src.connectors.base.CONNECTOR_REGISTRY") as mock_registry:
            mock_connector_cls = MagicMock()
            mock_connector = AsyncMock()
            mock_connector.cursor_type = "opaque"
            mock_connector_cls.return_value = mock_connector
            mock_registry.get.return_value = mock_connector_cls

            ok_empty_result = PollResult(events=[], cursor="new_cursor_789", error_class="none")
            mock_connector.poll = AsyncMock(return_value=ok_empty_result)

            from src.orchestrator.jarvis import JarvisOrchestrator

            orchestrator = MagicMock(spec=JarvisOrchestrator)
            orchestrator._settings = make_mock_settings()

            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.first = MagicMock(return_value=None)
            mock_db.execute = AsyncMock(return_value=mock_result)
            orchestrator._db_factory = MagicMock(return_value=mock_db)

            # OAuthManager is a local import inside _poll_connector; patch the module it lives in
            with patch("src.services.oauth_manager.OAuthManager") as mock_oauth_cls:
                mock_oauth = AsyncMock()
                mock_oauth.get_valid_token = AsyncMock(return_value="tok")
                mock_oauth_cls.return_value = mock_oauth

                (
                    events,
                    new_cursor,
                    poll_error,
                    cursor_type,
                ) = await JarvisOrchestrator._poll_connector(
                    orchestrator, "gmail", TEST_USER_ID, "ws_test"
                )

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
