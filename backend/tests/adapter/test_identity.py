"""Unit tests for adapter identity verification.

Pure-function tests: no DB, no I/O, no mocks needed. Uses the real
mint_platform_jwt/verify_platform_jwt pair so the test exercises the actual
RS256 sign/verify round trip.
"""

import pytest

from src.adapter.identity import AdapterPrincipal, IdentityError, verify_principal
from src.orchestrator.platform_jwt import mint_platform_jwt


def test_verify_principal_returns_adapter_principal_from_valid_token():
    token = mint_platform_jwt(
        principal_id="usr_1",
        tenant_id="ws_1",
        workspace_id="ws_1",
        capabilities=["email.search"],
    )

    principal = verify_principal(token)

    assert isinstance(principal, AdapterPrincipal)
    assert principal.principal_id == "usr_1"
    assert principal.tenant_id == "ws_1"
    assert principal.workspace_id == "ws_1"
    assert principal.capabilities == ("email.search",)


def test_verify_principal_raises_identity_error_for_malformed_token():
    with pytest.raises(IdentityError):
        verify_principal("not-a-jwt")
