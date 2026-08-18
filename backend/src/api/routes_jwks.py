"""JWKS endpoint exposing the platform JWT's public signing key.

ToolHive verifies Jarvis-minted platform JWTs (`platform_jwt.py`) against
this JWKS document rather than a shared secret, so the gateway never needs
Jarvis's private key.
"""

from fastapi import APIRouter

from src.orchestrator.platform_jwt import get_jwks

router = APIRouter(tags=["auth"])


@router.get("/.well-known/jwks.json")
async def jwks() -> dict:
    return get_jwks()
