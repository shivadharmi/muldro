"""Tests for the OpenConnector gateway settings defaults.

Every gateway coordinate defaults to ``None`` so an unconfigured deployment
fails LOUDLY at session-open (``GatewayNotConfigured``) rather than silently
half-working. There is deliberately no on/off feature flag — the migrated
installations have no native transport to fall back to. That the retired
``gmail_via_gateway`` flag stays gone is pinned in
``tests/integrations/test_native_retirement.py``.
"""

from src.config.settings import Settings

# `_env_file=None` on every one of these: a bare ``Settings()`` reads the
# developer's own backend/.env, so without it these assert "my .env happens not
# to set this", not "the default is None" — and they flip to red the moment
# anyone configures a local gateway. The isolated form is what the admin-settings
# tests below already use.


def test_toolhive_vmcp_url_defaults_none():
    assert Settings(_env_file=None).toolhive_vmcp_url is None


def test_openconnector_mcp_url_defaults_none():
    assert Settings(_env_file=None).openconnector_mcp_url is None


def test_openconnector_runtime_token_defaults_none():
    assert Settings(_env_file=None).openconnector_runtime_token is None


def test_platform_jwt_private_pem_defaults_none():
    assert Settings(_env_file=None).platform_jwt_private_pem is None


def test_openconnector_admin_settings_default_none():
    from src.config.settings import Settings

    s = Settings(_env_file=None)
    assert s.openconnector_admin_url is None
    assert s.openconnector_admin_token is None


def test_openconnector_admin_settings_from_env(monkeypatch):
    monkeypatch.setenv("MULDRO_OPENCONNECTOR_ADMIN_URL", "http://oc:3000")
    monkeypatch.setenv("MULDRO_OPENCONNECTOR_ADMIN_TOKEN", "admtok")
    from src.config.settings import Settings

    s = Settings(_env_file=None)
    assert s.openconnector_admin_url == "http://oc:3000"
    assert s.openconnector_admin_token == "admtok"
