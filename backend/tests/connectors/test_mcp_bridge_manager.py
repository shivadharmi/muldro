from src.integrations.local_servers import build_local_server_specs


class _Settings:
    google_oauth_client_id = "cid"
    google_oauth_client_secret = "secret"


def test_specs_registered_for_google_workspace():
    specs = build_local_server_specs(_Settings())
    assert "google-workspace" in specs
