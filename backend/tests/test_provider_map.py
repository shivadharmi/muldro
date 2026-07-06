"""Tests for the canonical source<->provider<->server map."""

from src.integrations.provider_map import (
    provider_for_server,
    provider_for_source,
    servers_for_provider,
    sources_for_provider,
)


class TestProviderForSource:
    def test_gmail_maps_to_google(self):
        assert provider_for_source("gmail") == "google"

    def test_calendar_maps_to_google(self):
        assert provider_for_source("calendar") == "google"

    def test_other_source_is_identity(self):
        assert provider_for_source("slack") == "slack"
        assert provider_for_source("github") == "github"
        assert provider_for_source("notion") == "notion"


class TestServersForProvider:
    def test_google_servers(self):
        assert servers_for_provider("google") == ["google-workspace"]

    def test_github_servers(self):
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
    def test_google_sources(self):
        assert sources_for_provider("google") == ["gmail", "calendar"]

    def test_other_provider_is_identity(self):
        assert sources_for_provider("slack") == ["slack"]
        assert sources_for_provider("github") == ["github"]

    def test_roundtrip_source_provider_source(self):
        # Every source maps to a provider whose sources include it.
        for source in ("gmail", "calendar", "slack", "github", "notion"):
            provider = provider_for_source(source)
            assert source in sources_for_provider(provider)
