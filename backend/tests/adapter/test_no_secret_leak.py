import json

from src.adapter.enforcement import strip_secrets

FAKE = "ya29.FAKE-TEST-TOKEN-do-not-use"


def test_adapter_response_never_contains_tokens():
    raw = {
        "messages": [{"id": "1"}],
        "access_token": FAKE,
        "meta": {"refresh_token": FAKE, "authorization": f"Bearer {FAKE}"},
    }
    cleaned = strip_secrets(raw)
    blob = json.dumps(cleaned)
    assert FAKE not in blob
    assert "access_token" not in blob
    assert "refresh_token" not in blob
