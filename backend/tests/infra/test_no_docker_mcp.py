from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_compose_has_no_google_workspace_mcp_service():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "google-workspace-mcp" not in compose


def test_google_workspace_docker_dir_removed():
    assert not (ROOT / "infra/docker/google-workspace-mcp").exists()
