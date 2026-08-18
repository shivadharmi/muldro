"""Short-lived platform JWT mint/verify + JWKS for the ToolHive gateway.

Jarvis mints a short-lived (5 minute) RS256 JWT that scopes a single call
into the ToolHive vMCP gateway to a principal, tenant, workspace, and an
explicit capability list. ToolHive verifies the token against Jarvis's JWKS
endpoint (`get_jwks()`) rather than a shared secret, so the gateway never
needs Jarvis's private key.

Key material is split by ROLE. The minting process (the Jarvis API) sets
`settings.platform_jwt_private_pem`. A verify-only process — notably the
Connection Context Adapter, which is this design's tenant-isolation boundary —
sets `settings.platform_jwt_public_pem` instead and never holds the signing key:
verification is a public-key operation, and handing the boundary the private half
would mean anything that compromised it could mint a token for any tenant.

With neither set, an ephemeral RSA-2048 key is generated on first use for
local/dev and a warning is logged. An ephemeral key is single-process only
(tokens are unverifiable across processes/restarts), so a stable PEM is required
before any multi-replica/HA deployment — a hard startup guard for that is a GA
prerequisite (spec §12).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

ISSUER = "jarvis-auth"
DEFAULT_AUDIENCE = "toolhive-vmcp"
KEY_ID = "jarvis-platform-1"
TOKEN_TTL_SECONDS = 300


class MissingSigningKeyError(RuntimeError):
    """Raised when minting is attempted in a verify-only process.

    A process configured with ``platform_jwt_public_pem`` and no private key can
    verify tokens but must never mint them — surfacing that as an explicit error
    beats silently signing with an ephemeral key nothing else can verify.
    """


def _load_or_generate_private_key() -> rsa.RSAPrivateKey:
    """Load the configured RSA private key, or generate an ephemeral one.

    Reads `settings.platform_jwt_private_pem`; when unset, falls back to an
    ephemeral key and logs a warning (dev/single-process only).
    """
    settings = get_settings()
    pem = settings.platform_jwt_private_pem
    if pem:
        key_bytes = pem.encode("utf-8") if isinstance(pem, str) else pem
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise ValueError("platform_jwt_private_pem must decode to an RSA private key")
        return private_key

    if settings.platform_jwt_public_pem:
        # Verify-only process (the adapter): it holds the public half by design and
        # must not fabricate a signing key. Minting raises MissingSigningKeyError.
        raise MissingSigningKeyError(
            "platform_jwt_public_pem is set but platform_jwt_private_pem is not — this "
            "process can verify platform JWTs but cannot mint them."
        )

    # Dev fallback: ephemeral key, regenerated every process start. Tokens
    # minted by one process cannot be verified by another in this mode. Warn
    # loudly; a hard startup guard for non-dev is a GA prerequisite (spec §12).
    logger.warning(
        "platform_jwt_private_pem is not set — using an ephemeral RSA key. Tokens are "
        "unverifiable across processes/restarts; set JARVIS_PLATFORM_JWT_PRIVATE_PEM before "
        "any multi-replica or production deployment."
    )
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _load_public_key() -> rsa.RSAPublicKey | None:
    """Load the configured RSA public key, or None when unset.

    Present so a verify-only process — the Connection Context Adapter, which is the
    tenant-isolation boundary — never needs the signing key. Verification is a
    public-key operation; giving the boundary the private half would mean anything
    that compromised it could mint a token for any tenant.
    """
    pem = get_settings().platform_jwt_public_pem
    if not pem:
        return None
    key_bytes = pem.encode("utf-8") if isinstance(pem, str) else pem
    public_key = serialization.load_pem_public_key(key_bytes)
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("platform_jwt_public_pem must decode to an RSA public key")
    return public_key


# Keys are resolved lazily and cached: a verify-only process must be able to import
# this module without a signing key, and an eager load would raise (or generate an
# ephemeral key) at import time for a process that will only ever verify.
_private_key: rsa.RSAPrivateKey | None = None
_public_key: rsa.RSAPublicKey | None = None


def _signing_key_pem() -> bytes:
    """The PEM used to SIGN. Only the minting process reaches this."""
    global _private_key
    if _private_key is None:
        _private_key = _load_or_generate_private_key()
    return _private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _verifying_key() -> rsa.RSAPublicKey:
    """The public key used to VERIFY and to publish JWKS.

    An explicitly configured ``platform_jwt_public_pem`` wins, so a verify-only
    process never touches the signing key. Otherwise it is derived from the private
    key — the minting process's own path, unchanged.
    """
    global _public_key, _private_key
    if _public_key is None:
        _public_key = _load_public_key()
    if _public_key is not None:
        return _public_key
    if _private_key is None:
        _private_key = _load_or_generate_private_key()
    return _private_key.public_key()


def mint_platform_jwt(
    *,
    principal_id: str,
    tenant_id: str,
    workspace_id: str,
    capabilities: list[str],
    authorization_source: str = "direct_user_request",
) -> str:
    """Mint a short-lived RS256 platform JWT scoping one gateway call.

    Args:
        principal_id: The acting principal (user/agent) id — becomes `sub`.
        tenant_id: Tenant scope for the call.
        workspace_id: Workspace scope for the call.
        capabilities: Explicit capability list authorized for this token.
        authorization_source: Provenance of the authorization (defaults to
            direct user request, matching the chat-path authorization model).

    Returns:
        The encoded JWT string, valid for `TOKEN_TTL_SECONDS` (5 minutes).
    """
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": principal_id,
        "aud": DEFAULT_AUDIENCE,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "authorization_source": authorization_source,
        "capabilities": capabilities,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(
        claims,
        _signing_key_pem(),
        algorithm="RS256",
        headers={"kid": KEY_ID},
    )


def verify_platform_jwt(token: str, *, audience: str) -> dict[str, Any]:
    """Verify a platform JWT and return its claims.

    Raises `jwt.InvalidTokenError` (or a subclass, e.g. `InvalidAudienceError`,
    `ExpiredSignatureError`) if the token is invalid, expired, or was minted
    for a different audience.
    """
    return jwt.decode(
        token,
        _verifying_key(),
        algorithms=["RS256"],
        audience=audience,
        issuer=ISSUER,
    )


def get_jwks() -> dict[str, Any]:
    """Return the JWKS document exposing the platform's public signing key."""
    public_numbers = _verifying_key().public_numbers()
    n = public_numbers.n
    e = public_numbers.e
    n_bytes = n.to_bytes((n.bit_length() + 7) // 8, byteorder="big")
    e_bytes = e.to_bytes((e.bit_length() + 7) // 8, byteorder="big")

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KEY_ID,
                "n": _b64url_uint(n_bytes),
                "e": _b64url_uint(e_bytes),
            }
        ]
    }


def _b64url_uint(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
