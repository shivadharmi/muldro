"""Tests for the canonical source<->provider<->server map."""

from src.integrations.provider_map import (
    provider_for_server,
    provider_for_source,
    servers_for_provider,
    sources_for_provider,
)


class TestProviderForSource:
    def test_gateway_backed_sources_are_identity(self):
        """gmail/calendar no longer fan out from a native ``google`` provider.

        They moved behind the OpenConnector gateway, so every native caller of
        this function short-circuits on ``gateway_provider_for_source`` before
        reaching it. The identity answer is what "no native provider owns this"
        looks like — ``gateway_actions`` is the map that answers for them.
        """
        assert provider_for_source("gmail") == "gmail"
        assert provider_for_source("calendar") == "calendar"

    def test_other_source_is_identity(self):
        assert provider_for_source("slack") == "slack"
        assert provider_for_source("github") == "github"
        assert provider_for_source("notion") == "notion"


class TestServersForProvider:
    def test_gateway_migrated_providers_are_not_listed(self):
        """google/github have no native OAuth provider owning an MCP server any
        more — the gateway registry (``gateway_actions.providers_for_server``)
        answers for them, so this map deliberately does not. They therefore hit
        the identity fallback rather than naming a server."""
        assert servers_for_provider("google") == ["google"]
        assert servers_for_provider("github") == ["github"]

    def test_slack_servers(self):
        assert servers_for_provider("slack") == ["slack"]

    def test_notion_servers(self):
        assert servers_for_provider("notion") == ["notion"]

    def test_atlassian_servers(self):
        assert servers_for_provider("atlassian") == ["atlassian"]

    def test_unknown_provider_falls_back_to_itself(self):
        assert servers_for_provider("mystery") == ["mystery"]


class TestProviderForServer:
    def test_google_workspace(self):
        assert provider_for_server("google-workspace") == "google"

    def test_gmail_named_server(self):
        assert provider_for_server("gmail") == "google"

    def test_calendar_named_server(self):
        assert provider_for_server("calendar") == "google"

    def test_github(self):
        assert provider_for_server("github") == "github"

    def test_slack(self):
        assert provider_for_server("slack") == "slack"

    def test_notion(self):
        assert provider_for_server("notion") == "notion"

    def test_atlassian_aliases(self):
        assert provider_for_server("jira") == "atlassian"
        assert provider_for_server("confluence") == "atlassian"
        assert provider_for_server("atlassian") == "atlassian"

    def test_unknown_server_returns_itself(self):
        assert provider_for_server("custom_server") == "custom_server"


class TestSourcesForProvider:
    def test_no_provider_fans_out_today(self):
        """``_PROVIDER_SOURCES`` is empty: every provider backs one same-named source.

        The only fan-out entry was ``google -> [gmail, calendar]``, retired with
        the gateway migration. Its sole reader (``ReauthService`` pause/resume)
        is reachable only for natively-authenticated providers, and a
        ``platform_jwt`` installation cannot raise ``McpAuthRequiredError``.
        """
        assert sources_for_provider("google") == ["google"]

    def test_other_provider_is_identity(self):
        assert sources_for_provider("slack") == ["slack"]
        assert sources_for_provider("github") == ["github"]

    def test_roundtrip_source_provider_source(self):
        # Every source maps to a provider whose sources include it.
        for source in ("gmail", "calendar", "slack", "github", "notion"):
            provider = provider_for_source(source)
            assert source in sources_for_provider(provider)
