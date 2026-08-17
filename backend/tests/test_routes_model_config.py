from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_current_user_id, get_current_workspace_id
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


def _client():
    app = create_app()
    mock_user = MagicMock()
    mock_user.user_id = TEST_USER_ID
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    app.dependency_overrides[get_current_workspace_id] = lambda: TEST_WORKSPACE_ID
    return TestClient(app)


def test_get_model_catalog():
    with _client() as c:
        r = c.get("/v1/model-catalog")
        assert r.status_code == 200
        body = r.json()
        assert "anthropic" in body["providers"]
        anthropic = body["providers"]["anthropic"]
        assert any(m["model_id"] == "claude-sonnet-4-6" for m in anthropic)
        assert all(
            {"model_id", "display_name", "thinking_style", "accepts_temperature", "suggested_tier"}
            <= set(m)
            for m in anthropic
        )
