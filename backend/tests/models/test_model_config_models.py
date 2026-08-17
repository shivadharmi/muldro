from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential


def test_provider_credential_columns():
    cols = ProviderCredential.__table__.columns.keys()
    assert {
        "id",
        "workspace_id",
        "provider",
        "api_key_encrypted",
        "base_url",
        "extra_config",
        "status",
        "enabled",
    } <= set(cols)
    assert ProviderCredential.__table__.columns["workspace_id"].nullable is True


def test_model_binding_columns():
    cols = ModelBinding.__table__.columns.keys()
    assert {
        "id",
        "workspace_id",
        "scope_type",
        "scope_key",
        "provider",
        "model_id",
        "effort",
        "max_tokens",
        "temperature",
        "params",
        "enabled",
    } <= set(cols)
    assert ModelBinding.__table__.columns["workspace_id"].nullable is True
