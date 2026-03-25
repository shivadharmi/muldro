"""Tests for IntegrationControlPlane and IntegrationInstallation model."""

from src.models.ids import generate_id, validate_typed_id
from src.models.integration_installation import IntegrationInstallation


class TestIntegrationInstallation:
    def test_generate_install_id(self):
        install_id = generate_id("inst")
        assert install_id.startswith("inst_")
        assert validate_typed_id(install_id, "inst")

    def test_create_installation(self):
        inst = IntegrationInstallation(
            install_id=generate_id("inst"),
            workspace_id="ws_test",
            user_id="usr_test",
            server_name="github",
            display_name="GitHub",
            transport="stdio",
            command="docker",
            args=["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"],
            env_template={"GITHUB_PERSONAL_ACCESS_TOKEN": "token"},
            status="active",
            health_status="unknown",
            enabled=True,
        )
        assert inst.server_name == "github"
        assert inst.transport == "stdio"
        assert inst.command == "docker"
        assert inst.enabled is True
        assert inst.status == "active"

    def test_transport_types(self):
        for transport in ["stdio", "sse", "streamable-http"]:
            inst = IntegrationInstallation(
                install_id=generate_id("inst"),
                workspace_id="ws_test",
                user_id="usr_test",
                server_name=f"test-{transport}",
                display_name=f"Test {transport}",
                transport=transport,
            )
            assert inst.transport == transport

    def test_scopes_granted(self):
        inst = IntegrationInstallation(
            install_id=generate_id("inst"),
            workspace_id="ws_test",
            user_id="usr_test",
            server_name="slack",
            display_name="Slack",
            scopes_granted=["messaging.send", "messaging.reply"],
        )
        assert inst.scopes_granted == ["messaging.send", "messaging.reply"]

    def test_optional_fields(self):
        inst = IntegrationInstallation(
            install_id=generate_id("inst"),
            workspace_id="ws_test",
            user_id="usr_test",
            server_name="minimal",
            display_name="Minimal",
        )
        assert inst.command is None
        assert inst.args is None
        assert inst.remote_url is None
        assert inst.trust_id is None
        assert inst.auth_provider is None


class TestSeedInstallations:
    def test_default_installations_count(self):
        from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS

        assert len(_DEFAULT_INSTALLATIONS) == 9

    def test_all_installations_have_required_fields(self):
        from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS

        for inst in _DEFAULT_INSTALLATIONS:
            assert "server_name" in inst
            assert "display_name" in inst
            assert inst.get("transport", "stdio") in ("stdio", "sse", "streamable-http")

    def test_server_names_unique(self):
        from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS

        names = [i["server_name"] for i in _DEFAULT_INSTALLATIONS]
        assert len(names) == len(set(names)), "Duplicate server names found"
