from src.integrations.local_servers import build_local_server_specs


class _Settings:
    google_oauth_client_id = "cid"
    google_oauth_client_secret = "secret"


def test_google_workspace_spec_uses_uvx_and_oauth21_env():
    specs = build_local_server_specs(_Settings())
    gw = specs["google-workspace"]
    assert gw.argv[:2] == ["uvx", "workspace-mcp"]
    assert "--transport" in gw.argv and "streamable-http" in gw.argv
    assert gw.env["MCP_ENABLE_OAUTH21"] == "true"
    assert gw.env["EXTERNAL_OAUTH21_PROVIDER"] == "true"
    assert gw.env["WORKSPACE_MCP_STATELESS_MODE"] == "true"
    assert gw.env["GOOGLE_OAUTH_CLIENT_ID"] == "cid"
    assert gw.env["GOOGLE_OAUTH_CLIENT_SECRET"] == "secret"
    assert gw.path == "/mcp"
