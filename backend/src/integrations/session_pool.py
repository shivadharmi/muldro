"""Per-user MCP session pool — manages authenticated Client instances.

Each (workspace_id, server_name, user_id) triple gets its own Client with
the user's OAuth token injected. Sessions are lazily created and TTL-cleaned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client
from fastmcp.client.auth import BearerAuth

from src.integrations.gateway_actions import (
    PROVIDER_REGISTRY,
    capabilities_for_server,
    providers_for_server,
)
from src.integrations.gateway_naming import action_id_to_tool_name
from src.integrations.local_process_manager import get_local_process_manager
from src.integrations.mcp_errors import McpAuthRequiredError
from src.integrations.provider_map import provider_for_server
from src.integrations.turn_scope import current_turn_scope
from src.services.mcp_resilience import MCPCircuitBreaker

logger = logging.getLogger(__name__)

# Default idle timeout before a session is cleaned up (30 minutes)
SESSION_TTL_SECONDS = 1800

# Per-call timeout for HTTP MCP tool discovery (list_tools). Kept as a
# module-level constant so tests can monkeypatch it to avoid real-time waits.
HTTP_DISCOVERY_TIMEOUT_SECONDS = 15

# Mapping: server_name → env var name for stdio token injection.
# Google Workspace excluded — it uses file-based auth, not raw tokens.
#
# Security note: Tokens are injected via environment variables because the
# MCP stdio transport protocol requires servers to be spawned as subprocesses.
# Env vars are visible in `ps aux` output — accepted trade-off because
# stdin-based token passing would break MCP server compatibility.
# The sessions are short-lived (30-min TTL) and per-user.
_STDIO_TOKEN_ENV_VARS: dict[str, str] = {
    "github": "GITHUB_PERSONAL_ACCESS_TOKEN",
    "slack": "SLACK_MCP_XOXB_TOKEN",
    "notion": "NOTION_TOKEN",
}

# OAuthManager token-reason values that mean the credential is permanently
# unusable and the user must reconnect (vs. "refresh_failed" which is transient).
_PERMANENT_REAUTH_REASONS: frozenset[str] = frozenset({"no_token", "no_refresh_token", "revoked"})

# Safety window before a bound platform JWT's expiry at which a cached session
# is proactively rebuilt (Gmail gateway). Larger than any single tool call so a
# call started just under the wire still completes on a valid bearer.
_PLATFORM_JWT_REFRESH_MARGIN_SECONDS = 30


def _platform_jwt_exp(token: str) -> float | None:
    """Return a platform JWT's ``exp`` (epoch seconds) without verifying it.

    Signature verification is unnecessary here — the token was just minted by
    this process; we only need its expiry to decide when to rebuild the cached
    session. Returns None if the token is unparseable or carries no ``exp``.
    """
    import jwt

    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return None
    exp = claims.get("exp")
    return float(exp) if exp is not None else None


def _gateway_owned_tool_names(server_name: str) -> frozenset[str] | None:
    """Registry-owned tool names for a gateway-backed server, or None if not one.

    ``None`` (not an empty set) means the server is NOT gateway-backed and its
    discovery response must be taken verbatim — auto-registering unknown MCP
    tools is deliberate behaviour for ordinary MCP servers.

    A server is gateway-backed exactly when ``providers_for_server`` is
    non-empty, the same signal ``services/integration_status.py`` uses.
    """
    provider_ids = providers_for_server(server_name)
    if not provider_ids:
        return None
    return frozenset(
        action_id_to_tool_name(a.action_id)
        for provider_id in provider_ids
        for a in PROVIDER_REGISTRY[provider_id].actions
    )


def _narrow_discovered_tools(raw_tools: list, server_name: str) -> list:
    """Narrow a gateway server's discovery response to the tools it actually owns.

    WHY: ONE OpenConnector gateway adapter endpoint serves SEVERAL Muldro
    installations (google-workspace and github both resolve to the same
    ``/mcp`` URL). ``list_tools()`` therefore returns the union of every
    provider's named tools plus the generic ``execute_action`` /
    ``list_connections`` escape hatches — a discovery response is NOT
    per-installation. Taken verbatim, whichever gateway installation is
    discovered FIRST claims every name, so ``get_server_for_tool`` resolves
    e.g. ``gmail_get_profile`` to ``github``, a github session is opened, and
    its platform JWT is minted from github's capabilities only — the adapter's
    capability gate then refuses the call.

    Narrowing by the registry restores the per-installation view. Non-gateway
    servers are returned unchanged.
    """
    owned = _gateway_owned_tool_names(server_name)
    if owned is None:
        return raw_tools
    narrowed = [t for t in raw_tools if t.name in owned]
    if raw_tools and not narrowed:
        logger.warning(
            "[mcp:session] gateway server %s discovered %d tool(s), none of which "
            "the gateway registry recognises (registry owns %d name(s)) — "
            "registering an empty tool map",
            server_name,
            len(raw_tools),
            len(owned),
        )
    return narrowed


@dataclass
class SessionEntry:
    """A tracked MCP client session."""

    client: Client
    client_ctx: Any  # The async context manager
    server_name: str
    user_id: str
    tools: dict[str, str]  # canonical_name → raw_mcp_name
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    # OAuth access token the Client was built with, if any. Recorded so
    # cached sessions can be cycled when OAuthManager returns a newer token
    # (e.g., after a background refresh) — preventing "stale bearer bound
    # to a live Client" failures that manifest as Atlassian's generic
    # "We are having trouble..." error.
    bound_token: str | None = None
    # Wall-clock (epoch seconds) expiry of the bound bearer, when it is a
    # self-describing token (the platform JWT used by the Gmail gateway).
    # Lets a cached session be rebuilt before its short-lived JWT expires,
    # rather than reusing a dead bearer mid-turn. None for non-JWT bearers.
    bound_token_exp: float | None = None
    # Name of the locally-managed MCP process this session uses (if any), so
    # every teardown path releases the process refcount exactly once.
    managed_server: str | None = None


class UserMCPSessionPool:
    """Pool of per-user MCP Client instances with auth and circuit breaking.

    Usage:
        pool = UserMCPSessionPool(oauth_manager, circuit_breaker)
        result = await pool.call_tool(
            "gmail_send", {...}, user_id="usr_1", server_name="google-workspace",
        )
    """

    def __init__(
        self,
        oauth_manager: Any | None = None,
        circuit_breaker: MCPCircuitBreaker | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
    ) -> None:
        self._oauth_manager = oauth_manager
        self._circuit_breaker = circuit_breaker or MCPCircuitBreaker()
        self._ttl_seconds = ttl_seconds
        # (workspace_id, server_name, user_id) → SessionEntry
        self._sessions: dict[tuple[str, str, str], SessionEntry] = {}
        self._lock = asyncio.Lock()
        # (workspace_id, server_name) → config dict
        self._server_configs: dict[tuple[str, str], dict] = {}
        # (workspace_id, server_name) → tool mapping (canonical → raw)
        self._server_tools: dict[tuple[str, str], dict[str, str]] = {}
        # Keyed by (workspace_id, server_name, tool_name): a tool's identity is
        # the triple, not the bare name. Two servers may legitimately serve the
        # same tool name (e.g. two gateway installations behind one adapter
        # endpoint) and must coexist rather than overwrite.
        self._tool_metadata: dict[tuple[str, str, str], dict[str, Any]] = {}

    def register_server_config(
        self,
        server_name: str,
        config: dict,
        workspace_id: str = "",
    ) -> None:
        """Register server configuration for later session creation."""
        self._server_configs[(workspace_id, server_name)] = config

    def _effective_user(self, server_name: str, user_id: str, workspace_id: str = "") -> str:
        """Resolve the session-key user: auth-free servers share one __shared__ session."""
        config = self._server_configs.get((workspace_id, server_name))
        auth_provider = (config or {}).get("auth_provider", "none")
        return "__shared__" if auth_provider == "none" else user_id

    async def get_or_create_session(
        self,
        server_name: str,
        user_id: str,
        workspace_id: str = "",
    ) -> SessionEntry:
        """Get an existing session or create a new one with auth.

        Auth-free servers share a single session per workspace (no per-user
        subprocess needed), keyed with a ``__shared__`` sentinel user_id.
        """
        config = self._server_configs.get((workspace_id, server_name))
        auth_provider = (config or {}).get("auth_provider", "none")
        effective_user = self._effective_user(server_name, user_id, workspace_id)
        key = (workspace_id, server_name, effective_user)

        # If we already have a cached session for an OAuth-backed server,
        # verify the bound bearer is still the current valid token. The
        # OAuthManager refresh runs lazily, so a background refresh can
        # leave the cached Client holding a stale token — Atlassian's
        # hosted MCP answers those with a generic "having trouble" body
        # (not a 401), which is hard to recover from after the fact.
        # Checking up-front is a single DB read on the hot path, traded
        # for reliable token rotation.
        if self._is_oauth_server(server_name, workspace_id) and self._oauth_manager:
            entry = self._sessions.get(key)
            if entry and entry.bound_token:
                provider_name = (
                    auth_provider if auth_provider != "oauth" else _infer_provider(server_name)
                )
                try:
                    current = await self._oauth_manager.get_valid_token(user_id, provider_name)
                except Exception:
                    current = None
                if current and current != entry.bound_token:
                    logger.info(
                        "[mcp:session] token changed for %s/%s — rebuilding session",
                        server_name,
                        user_id,
                    )
                    await self.refresh_session(server_name, user_id, workspace_id=workspace_id)

        # Gmail gateway slice: a cached platform_jwt session holds a short-lived
        # (300s) bearer minted at creation. Unlike OAuth, the JWT is not
        # re-resolved on reuse, so a turn outliving the TTL would send an expired
        # bearer and the gateway would reject the call. Rebuild the session once
        # the bound JWT is within the refresh margin — the create path below
        # mints a fresh token.
        if auth_provider == "platform_jwt":
            entry = self._sessions.get(key)
            if (
                entry
                and entry.bound_token_exp is not None
                and time.time() + _PLATFORM_JWT_REFRESH_MARGIN_SECONDS >= entry.bound_token_exp
            ):
                logger.info(
                    "[mcp:session] platform JWT near expiry for %s/%s — rebuilding session",
                    server_name,
                    user_id,
                )
                await self.refresh_session(server_name, user_id, workspace_id=workspace_id)

        async with self._lock:
            entry = self._sessions.get(key)
            if entry:
                entry.last_used = time.monotonic()
                scope = current_turn_scope()
                if scope is not None:
                    scope.acquire(key)
                return entry

            # Create new session
            config = self._server_configs.get((workspace_id, server_name))
            if not config:
                raise RuntimeError(
                    f"No config registered for MCP server '{server_name}'. "
                    f"Call register_server_config() first."
                )

            # Shallow copy to avoid mutating the registered template
            config = dict(config)
            if "env" in config:
                config["env"] = dict(config["env"])

            # Resolve auth
            auth = await self._resolve_auth(server_name, user_id, config, workspace_id=workspace_id)
            bound_token: str | None = None
            bound_token_exp: float | None = None
            if auth is not None and isinstance(auth, BearerAuth):
                bound_token = (
                    auth.token.get_secret_value()
                    if hasattr(auth.token, "get_secret_value")
                    else str(auth.token)
                )
                # Only the platform JWT is self-describing; record its expiry so
                # the reuse path above can rebuild before it dies.
                if auth_provider == "platform_jwt" and bound_token:
                    bound_token_exp = _platform_jwt_exp(bound_token)

            # Create Client
            transport = config.get("transport", "stdio")
            managed_server: str | None = None
            if transport in ("sse", "streamable-http"):
                if config.get("managed_local"):
                    mgr = get_local_process_manager()
                    if mgr is None:
                        raise RuntimeError(
                            f"'{server_name}' is managed_local but no "
                            "LocalMCPProcessManager is configured"
                        )
                    url = await mgr.ensure_running(server_name)
                    managed_server = server_name
                else:
                    url = config["url"]
                client_ctx = Client(url, auth=auth) if auth else Client(url)
            else:
                # stdio transport — inject auth as env var, then build config.
                #
                # Guard: token-required stdio servers (slack/github/notion)
                # fatal-crash when spawned with no token — the npx subprocess
                # dumps raw Go/Node stack traces and exits. Refuse to spawn
                # when no usable bearer token was resolved (user hasn't
                # connected the integration). Raising here is handled at every
                # caller boundary (call_tool, discover_and_persist, OAuth
                # callback) as a recorded failure — never a crash.
                if _requires_stdio_token(server_name, config) and not _bearer_token(auth):
                    # McpAuthRequiredError subclasses ConnectionError, so existing
                    # `except ConnectionError` boundaries still catch it; the
                    # provider/reason fields let the re-auth service react.
                    raise McpAuthRequiredError(
                        provider=_infer_provider(server_name),
                        server=server_name,
                        reason="no_token",
                    )
                if auth and isinstance(auth, BearerAuth):
                    # BearerAuth wraps token in SecretStr; unwrap for env dict
                    raw_token = (
                        auth.token.get_secret_value()
                        if hasattr(auth.token, "get_secret_value")
                        else str(auth.token)
                    )
                    _inject_stdio_auth(config, server_name, raw_token)
                server_cfg = {"mcpServers": {server_name: config}}
                client_ctx = Client(server_cfg)

            # Connect and discover tools
            client = await client_ctx.__aenter__()
            raw_tools = await client.list_tools()

            # A gateway endpoint is shared by several installations, so narrow
            # its response to the tools the registry says THIS server owns
            # before anything is derived from it. Applied here so the single
            # narrowed list feeds _server_tools, _tool_metadata AND
            # _register_discovered_tools alike. See _narrow_discovered_tools.
            raw_tools = _narrow_discovered_tools(raw_tools, server_name)

            # Skip normalization — store real MCP names end-to-end
            tool_mapping = {}
            for t in raw_tools:
                tool_mapping[t.name] = t.name  # identity mapping
                input_schema = (
                    getattr(t, "inputSchema", None)
                    or getattr(t, "input_schema", None)
                    or {"type": "object", "properties": {}}
                )
                self._tool_metadata[(workspace_id, server_name, t.name)] = {
                    "name": t.name,
                    "server": server_name,
                    "description": t.description or "",
                    "input_schema": input_schema,
                    "_workspace_id": workspace_id,
                }
            self._server_tools[(workspace_id, server_name)] = tool_mapping

            # Register unknown discovered tools in DB with safe defaults
            await self._register_discovered_tools(raw_tools, server_name, workspace_id)

            entry = SessionEntry(
                client=client,
                client_ctx=client_ctx,
                server_name=server_name,
                user_id=user_id,
                tools=tool_mapping,
                bound_token=bound_token,
                bound_token_exp=bound_token_exp,
                managed_server=managed_server,
            )
            self._sessions[key] = entry
            scope = current_turn_scope()
            if scope is not None:
                scope.register(key)

            logger.info(
                "Created MCP session: server=%s user=%s tools=%d",
                server_name,
                user_id,
                len(tool_mapping),
            )
            return entry

    async def _register_discovered_tools(
        self, raw_tools: list, server_name: str, workspace_id: str
    ) -> None:
        """Register or enrich discovered tools in DB.

        New tools get capability=None (invisible to agents until admin maps
        capability). Existing tools (e.g., from seeds) get enriched with
        input_schema and description from MCP discovery — seeds have
        capability but lack schema; discovery provides schema.
        """
        try:
            from ulid import ULID

            from src.models.database import get_session_factory
            from src.models.tool_definitions import ToolBackend, ToolDefinition
            from src.services.tool_registry import ToolRegistry

            async with get_session_factory()() as db:
                registry = ToolRegistry(db, workspace_id=workspace_id or None)
                for t in raw_tools:
                    discovered_schema = (
                        getattr(t, "inputSchema", None)
                        or getattr(t, "input_schema", None)
                        or {"type": "object", "properties": {}}
                    )
                    discovered_desc = t.description or ""

                    existing = await registry.get_tool(t.name)
                    if not existing:
                        new_tool = ToolDefinition(
                            tool_id=f"tool_{ULID()}",
                            workspace_id=workspace_id or None,
                            name=t.name,
                            server=server_name,
                            backend=ToolBackend.EXTERNAL_MCP,
                            source="discovered",
                            capability=None,
                            risk_level="medium",
                            requires_approval=True,
                            description=discovered_desc,
                            input_schema=discovered_schema,
                            enabled=True,
                            verified=True,
                        )
                        db.add(new_tool)
                        logger.info(
                            "Registered discovered tool: %s from %s",
                            t.name,
                            server_name,
                        )
                    else:
                        # Enrich existing seed record with discovered
                        # schema/description (seeds lack these fields)
                        enriched = False
                        if not existing.input_schema and discovered_schema:
                            existing.input_schema = discovered_schema
                            enriched = True
                        if not existing.description and discovered_desc:
                            existing.description = discovered_desc
                            enriched = True
                        if enriched:
                            logger.info(
                                "Enriched tool %s with discovered metadata",
                                t.name,
                            )
                await db.commit()
        except Exception:
            logger.debug("Failed to register discovered tools", exc_info=True)

    async def call_tool(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        user_id: str,
        server_name: str,
        workspace_id: str = "",
        max_retries: int = 3,
    ) -> dict:
        """Call a tool on an external MCP server with auth, circuit breaking, and retry.

        Retries transient errors (timeout, rate limit, server error) with
        exponential backoff + jitter. Does not retry auth or validation errors.
        """
        import asyncio
        import random

        from src.integrations.mcp_errors import (
            MCPErrorCode,
            classify_error,
            is_insufficient_scope,
            is_transient,
            make_error_response,
        )

        # Circuit breaker check
        if not self._circuit_breaker.is_available(server_name):
            logger.warning(
                "[mcp:session] circuit OPEN for %s — rejecting %s",
                server_name,
                tool_name,
            )
            return {
                "status": "error",
                "error_code": "circuit_open",
                "message": f"MCP server '{server_name}' circuit open",
            }

        # Get or create session
        try:
            session = await self.get_or_create_session(
                server_name,
                user_id,
                workspace_id,
            )
        except McpAuthRequiredError as e:
            # Permanent "needs reconnect" — never crash; return a structured
            # auth_required envelope carrying provider/server so the caller can
            # trigger the re-auth flow.
            logger.warning(
                "[mcp:session] auth required for %s/%s: %s",
                server_name,
                tool_name,
                e,
            )
            return {
                "status": "error",
                "error": str(e),
                "error_code": MCPErrorCode.AUTH_REQUIRED,
                "provider": e.provider,
                "server": e.server,
            }
        except Exception as e:
            logger.warning(
                "[mcp:session] session creation failed for %s/%s: %s",
                server_name,
                tool_name,
                e,
            )
            return make_error_response(e, tool_name=tool_name)

        # Resolve canonical → raw MCP tool name
        raw_name = tool_name

        # Auto-inject per-server defaults (e.g., Atlassian's cloudId) into
        # the tool input. The agent doesn't need to know these values —
        # they're captured at OAuth time and persisted on the installation.
        # Caller-supplied keys always win so agents can still override
        # (e.g., targeting a different cloudId if the user has multiple).
        server_cfg = self._server_configs.get((workspace_id, server_name)) or {}
        tool_defaults = server_cfg.get("tool_defaults") or {}
        if tool_defaults:
            tool_input = {**tool_defaults, **tool_input}

        import time as _time

        call_start = _time.monotonic()
        last_error: Exception | None = None
        scope_failure = False
        attempt = 0
        for attempt in range(max_retries):
            try:
                result = await session.client.call_tool(raw_name, tool_input)
                self._circuit_breaker.record_success(server_name)
                session.last_used = time.monotonic()
                latency_ms = int((_time.monotonic() - call_start) * 1000)

                # Extract content from CallToolResult
                if hasattr(result, "content"):
                    text_parts = []
                    for block in result.content:
                        if hasattr(block, "text"):
                            text_parts.append(block.text)
                        elif hasattr(block, "data"):
                            text_parts.append(str(block.data))
                    output = "\n".join(text_parts)
                    logger.info(
                        "[mcp:session] %s on %s OK | %dms | %d chars",
                        tool_name,
                        server_name,
                        latency_ms,
                        len(output),
                    )
                    return {"status": "ok", "result": output}

                logger.info(
                    "[mcp:session] %s on %s OK | %dms",
                    tool_name,
                    server_name,
                    latency_ms,
                )
                return {"status": "ok", "result": str(result)}

            except Exception as e:
                last_error = e
                error_code = classify_error(e)
                # A permanent grant-scope failure: the user must re-consent with
                # a broader scope set. Refreshing the bearer fetches the SAME
                # narrow grant, so skip refresh and route to re-auth below.
                scope_failure = is_insufficient_scope(e)

                # Only retry transient errors
                if not is_transient(error_code) or attempt >= max_retries - 1:
                    self._circuit_breaker.record_failure(server_name)
                    # If the cached session was built with a stale OAuth
                    # bearer (auth error) or the server is OAuth-backed and
                    # we've exhausted retries, invalidate it so the next
                    # call rebuilds with a freshly fetched token. Without
                    # this, a revoked/expired token is resent repeatedly
                    # until the 5-min circuit cooldown elapses — at which
                    # point the same stale session is still cached.
                    # Only cycle the session when the failure could plausibly be
                    # session/token staleness. A client-fault error — VALIDATION
                    # (bad/missing args) or NOT_FOUND (missing resource) — has
                    # nothing to do with session health; refreshing on it would
                    # tear down the *shared* OAuth session (and release the managed
                    # process) out from under concurrent calls, cascading
                    # "Session task completed unexpectedly" to unrelated tools.
                    client_fault = error_code in (
                        MCPErrorCode.VALIDATION,
                        MCPErrorCode.NOT_FOUND,
                    )
                    should_refresh = (
                        not scope_failure
                        and not client_fault
                        and (
                            error_code == MCPErrorCode.AUTH_ERROR
                            or self._is_oauth_server(server_name, workspace_id)
                        )
                    )
                    if should_refresh:
                        try:
                            await self.refresh_session(
                                server_name,
                                user_id,
                                workspace_id=workspace_id,
                            )
                        except Exception:
                            logger.debug(
                                "Auto-refresh of %s session failed",
                                server_name,
                                exc_info=True,
                            )
                    break

                # A lost session will never recover by retrying the SAME dead
                # session — rebuild it (refresh drops the dead entry; re-acquire
                # spawns a fresh one) before the backoff retry.
                if error_code == MCPErrorCode.SESSION_LOST:
                    try:
                        await self.refresh_session(server_name, user_id, workspace_id=workspace_id)
                        session = await self.get_or_create_session(
                            server_name, user_id, workspace_id
                        )
                    except Exception:
                        logger.debug(
                            "Rebuild of %s session after session-loss failed",
                            server_name,
                            exc_info=True,
                        )

                # Exponential backoff with jitter: 1s, 2s, 4s
                delay = (2**attempt) + random.uniform(0, 0.5)
                logger.info(
                    "Retrying MCP tool '%s' (attempt %d/%d, %s), delay=%.1fs",
                    tool_name,
                    attempt + 1,
                    max_retries,
                    error_code,
                    delay,
                )
                await asyncio.sleep(delay)

        # Permanent grant-scope failure → emit the structured ``auth_required``
        # envelope (carrying provider+server) instead of a generic ``auth_error``.
        # This is the single signal the whole re-auth pipeline keys on: the
        # agent loop marks the integration unavailable and stops retrying it
        # (no runaway workaround loop), step_runner surfaces it into the step
        # output, and dag_runner defers the run for re-authorization + notifies.
        if scope_failure and last_error is not None:
            provider = _infer_provider(server_name)
            logger.warning(
                "[mcp:session] %s on %s needs re-authorization (insufficient scope) — provider=%s",
                tool_name,
                server_name,
                provider,
            )
            return {
                "status": "error",
                "error_code": MCPErrorCode.AUTH_REQUIRED,
                "error": (
                    f"Integration '{provider}' is missing the permissions this "
                    "action needs. Ask the user to reconnect it (re-authorize) "
                    "with the required scopes; do not retry."
                ),
                "provider": provider,
                "server": server_name,
                "reason": "insufficient_scope",
            }

        logger.warning(
            "MCP tool '%s' on '%s' failed after %d attempt(s): %s",
            tool_name,
            server_name,
            attempt + 1,
            last_error,
        )
        return make_error_response(
            last_error or RuntimeError("Unknown error"),
            tool_name=tool_name,
        )

    async def _release_managed(self, entry: SessionEntry) -> None:
        if not entry.managed_server:
            return
        mgr = get_local_process_manager()
        if mgr is not None:
            try:
                await mgr.release(entry.managed_server)
            except Exception:
                logger.debug("release of %s failed", entry.managed_server, exc_info=True)

    async def close_keys(self, keys: list[tuple[str, str, str]]) -> int:
        """Close specific sessions by key (used for per-turn teardown).

        Idempotent: keys with no live session are skipped. Releases any
        managed-local process refcount the session held.

        Invariant: the TurnScope refcount only gates *whether* teardown is
        attempted for a key; the actual ``__aexit__`` is gated by the entry
        still being present in ``_sessions`` (popped under the lock). So a
        key that was refreshed/closed mid-turn is simply skipped here — there
        is never a double ``__aexit__`` or double process release.
        """
        closed = 0
        async with self._lock:
            for key in keys:
                entry = self._sessions.pop(key, None)
                if not entry:
                    continue
                try:
                    await entry.client_ctx.__aexit__(None, None, None)
                except Exception:
                    logger.debug("close_keys: error closing %s", key, exc_info=True)
                await self._release_managed(entry)
                closed += 1
        if closed:
            logger.info("[mcp:session] closed %d session(s) at turn end", closed)
        return closed

    async def refresh_session(
        self,
        server_name: str,
        user_id: str,
        workspace_id: str = "",
    ) -> None:
        """Force reconnect a session (e.g., after OAuth token refresh)."""
        effective_user = self._effective_user(server_name, user_id, workspace_id)
        key = (workspace_id, server_name, effective_user)

        async with self._lock:
            entry = self._sessions.pop(key, None)
            if entry:
                try:
                    await entry.client_ctx.__aexit__(None, None, None)
                except Exception:
                    logger.debug("Error closing session %s/%s", server_name, user_id)
                await self._release_managed(entry)

        logger.info("Refreshed MCP session: server=%s user=%s", server_name, user_id)

    async def cleanup_idle(self) -> int:
        """Remove sessions that have been idle beyond TTL. Returns count removed."""
        now = time.monotonic()
        to_remove: list[tuple[str, str, str]] = []

        async with self._lock:
            for key, entry in self._sessions.items():
                if now - entry.last_used > self._ttl_seconds:
                    to_remove.append(key)

            for key in to_remove:
                entry = self._sessions.pop(key)
                try:
                    await entry.client_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                await self._release_managed(entry)

        if to_remove:
            logger.info("Cleaned up %d idle MCP sessions", len(to_remove))
        return len(to_remove)

    async def shutdown(self) -> None:
        """Gracefully close all sessions."""
        async with self._lock:
            for key, entry in list(self._sessions.items()):
                try:
                    await entry.client_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                await self._release_managed(entry)
            self._sessions.clear()
        logger.info("MCP session pool shut down")

    def unregister_server(self, server_name: str, workspace_id: str = "") -> None:
        """Remove all config, tool mappings, and metadata for a server.

        Called when a server is revoked so it cannot be rediscovered or reconnected.
        """
        self._server_configs.pop((workspace_id, server_name), None)
        removed_tools = self._server_tools.pop((workspace_id, server_name), {})
        for canonical in removed_tools:
            self._tool_metadata.pop((workspace_id, server_name, canonical), None)

    def has_server_config(self, server_name: str, workspace_id: str = "") -> bool:
        """Check if a server config is registered."""
        return (workspace_id, server_name) in self._server_configs

    def _is_oauth_server(self, server_name: str, workspace_id: str = "") -> bool:
        """Return True if the server's auth_provider resolves OAuth bearer tokens.

        Used to decide whether a terminal tool-call failure should invalidate
        the cached session — for OAuth servers, failures may be masking a
        stale bearer, and cycling the session is the cheap recovery path.
        """
        config = self._server_configs.get((workspace_id, server_name))
        if not config:
            return False
        auth_provider = config.get("auth_provider", "none")
        return auth_provider in {
            "oauth",
            "google",
            "github",
            "slack",
            "notion",
            "atlassian",
        }

    def is_pool_tool(self, tool_name: str, workspace_id: str = "") -> bool:
        """Check if a tool is known to any server in the pool."""
        for key, server_tools in self._server_tools.items():
            if workspace_id and key[0] != workspace_id:
                continue
            if tool_name in server_tools:
                return True
        return False

    def get_server_for_tool(self, tool_name: str, workspace_id: str = "") -> str | None:
        """Find which server provides a canonical tool name.

        Resolution is deterministic (lexicographically first server) rather than
        first-match-over-a-dict, because ``_server_tools`` is ordered by discovery
        and would otherwise resolve the same collision differently across
        restarts. A collision is also warned about: increment 2 hit exactly this
        shape when two gateway installations shared one MCP endpoint and every
        Gmail tool silently resolved to the ``github`` server.

        Candidates are deduplicated by server name before the ambiguity check.
        An unscoped lookup (``workspace_id=""``) walks every workspace, so one
        server installed in two workspaces yields the same name twice — that is
        not a collision, and warning about it would put noise on the exact
        channel this warning exists to keep clean.
        """
        candidates = sorted(
            {
                key[1]
                for key, tools in self._server_tools.items()
                if (not workspace_id or key[0] == workspace_id) and tool_name in tools
            }
        )
        if not candidates:
            return None
        if len(candidates) > 1:
            logger.warning(
                "[mcp:pool] tool %r is served by %d servers (%s) — resolving to %r; "
                "tool identity is (workspace, server, name), so a bare name is ambiguous",
                tool_name,
                len(candidates),
                ", ".join(candidates),
                candidates[0],
            )
        return candidates[0]

    def get_all_tools(self, workspace_id: str = "") -> dict[str, str]:
        """Return all tools across all servers: {canonical_name: server_name}."""
        result: dict[str, str] = {}
        for key, tools in self._server_tools.items():
            if workspace_id and key[0] != workspace_id:
                continue
            for canonical in tools:
                result[canonical] = key[1]  # server_name
        return result

    def get_all_tool_metadata(self, workspace_id: str = "") -> list[dict[str, Any]]:
        """Return all tool metadata across servers.

        Parameters that ``call_tool`` auto-injects from the installation's
        ``tool_defaults`` (e.g., Atlassian's cloudId) are stripped from the
        schema shown to agents. Otherwise the agent sees cloudId as
        ``required`` in the tool schema, reasons "I must get this from the
        user", and asks — even though the value is already known server-side.
        Stripping the key from ``required`` and ``properties`` removes that
        pressure while still letting the user pass it explicitly if they
        ever want to override (call_tool preserves caller-supplied keys).

        The key ``(workspace_id, server_name, tool_name)`` is the authoritative
        identity, so both the workspace filter and the ``_server_configs``
        lookup read it rather than the duplicated ``_workspace_id`` payload
        value. Reading the key is what makes an unscoped call (``workspace_id=""``,
        meaning "no filtering") still find each row's own installation config —
        otherwise ``tool_defaults`` were never stripped and the agent saw
        injected params as required. An empty key workspace still passes any
        filter, preserving the "global rows are always visible" behaviour.
        """
        result: list[dict[str, Any]] = []
        for key, meta in self._tool_metadata.items():
            row_workspace = key[0]
            if workspace_id and row_workspace and row_workspace != workspace_id:
                continue
            item = dict(meta)
            item["name"] = key[2]

            server_name = key[1]
            server_cfg = self._server_configs.get((row_workspace, server_name)) or {}
            tool_defaults = server_cfg.get("tool_defaults") or {}
            if tool_defaults:
                item["input_schema"] = _strip_injected_params(
                    item.get("input_schema"),
                    set(tool_defaults.keys()),
                )

            result.append(item)
        return result

    def get_health(self) -> dict[str, dict]:
        """Get health status for all servers in the pool."""
        health: dict[str, dict] = {}
        for (workspace_id, server_name, user_id), entry in self._sessions.items():
            if server_name not in health:
                health[server_name] = {
                    "sessions": 0,
                    "tools": len(entry.tools),
                    "circuit_available": self._circuit_breaker.is_available(server_name),
                }
            health[server_name]["sessions"] += 1
        return health

    async def _resolve_auth(
        self,
        server_name: str,
        user_id: str,
        config: dict,
        workspace_id: str = "",
    ) -> BearerAuth | str | None:
        """Resolve authentication for a server connection."""
        auth_provider = config.get("auth_provider", "none")

        if auth_provider == "platform_jwt":
            # Gateway slice: mint a fresh short-lived platform JWT for the
            # ToolHive vMCP instead of resolving a stored OAuth/static token. The
            # JWT's tenant_id MUST match how connection_map rows are keyed (the
            # workspace_id) so the downstream adapter can resolve the caller's
            # connection. Falls back to user_id only when workspace_id is absent
            # (the one-user-one-workspace invariant).
            from src.orchestrator.platform_jwt import mint_platform_jwt

            tenant = workspace_id or user_id
            # Capabilities are DERIVED from the gateway_actions registry as the
            # union across the providers this installation serves (see
            # capabilities_for_server), so a GitHub session's token carries no
            # email capability and vice versa — which is what makes the
            # adapter's per-action capability gate load-bearing ACROSS
            # installations, not just within one. An unregistered server_name
            # mints an empty capability list (fail-closed): it must not
            # inherit another installation's capabilities.
            #
            # Known remaining limitation (separate scheduled increment, not
            # fixed here): the token is minted once per SESSION creation,
            # cached by SessionKey across steps, and rebuilt only near
            # bound_token_exp — so it is not step-scoped. Narrowing to the
            # current step's capability would need either a capability-keyed
            # session key or a per-call re-mint, plus a ContextVar that does
            # not exist today. WITHIN one installation, the deep runtime's
            # capability_scope middleware is the first-line guard.
            capabilities = list(capabilities_for_server(server_name))
            if not capabilities:
                # The two gateway-ness signals have diverged: this installation
                # DECLARES auth_provider="platform_jwt" (so it routes to the
                # vMCP) but the registry knows no providers for its
                # server_name. The token mints empty, so every gateway call it
                # makes will be denied at the adapter's capability gate — a
                # useless installation. Registry invariant tests pin the seeded
                # set; this catches a DB row that drifted from it.
                logger.error(
                    "Installation %r declares auth_provider='platform_jwt' but the gateway "
                    "registry knows no providers for it — minting an EMPTY capability set, "
                    "so every gateway call for this server will be denied.",
                    server_name,
                )
            token = mint_platform_jwt(
                principal_id=user_id,
                tenant_id=tenant,
                workspace_id=tenant,
                capabilities=capabilities,
            )
            return BearerAuth(token=token)

        if auth_provider == "none":
            return None

        if auth_provider == "token":
            # Static token from config
            token = config.get("token", "")
            return BearerAuth(token=token) if token else None

        if auth_provider in ("oauth", "google", "github", "slack", "notion", "atlassian"):
            # Resolve OAuth token from OAuthManager
            if not self._oauth_manager:
                logger.warning("OAuth requested but no OAuthManager configured")
                return None

            # Map server auth_provider to OAuth provider name
            provider_name = (
                auth_provider if auth_provider != "oauth" else _infer_provider(server_name)
            )
            try:
                result = await self._oauth_manager.get_valid_token_with_reason(
                    user_id, provider_name
                )
            except McpAuthRequiredError:
                raise
            except Exception as e:
                # Treat an unexpected lookup failure as transient — return None
                # so the caller skips (rather than escalating to re-auth on a
                # DB/network blip).
                logger.warning("OAuth token resolution failed: %s", e)
                return None

            if result.reason == "ok" and result.token:
                return BearerAuth(token=result.token)
            if result.reason in _PERMANENT_REAUTH_REASONS:
                # User must reconnect — signal it explicitly.
                raise McpAuthRequiredError(
                    provider=provider_name,
                    server=server_name,
                    reason=result.reason,
                )
            # "refresh_failed" (or token-less "ok") — transient; skip this call.
            logger.warning(
                "No usable OAuth token for user=%s provider=%s (reason=%s)",
                user_id,
                provider_name,
                result.reason,
            )

        return None


def _strip_injected_params(schema: Any, keys: set[str]) -> dict:
    """Return a copy of ``schema`` with auto-injected keys removed.

    Only rewrites ``properties`` and ``required`` at the top level — those
    are the fields the Claude API uses to decide what the agent must
    provide. Nested definitions are left untouched.
    """
    if not isinstance(schema, dict) or not keys:
        return schema if isinstance(schema, dict) else {}

    new_schema = dict(schema)

    props = new_schema.get("properties")
    if isinstance(props, dict):
        new_schema["properties"] = {k: v for k, v in props.items() if k not in keys}

    required = new_schema.get("required")
    if isinstance(required, list):
        new_schema["required"] = [k for k in required if k not in keys]

    return new_schema


def _requires_stdio_token(server_name: str, config: dict) -> bool:
    """Return True if a stdio server cannot run without an injected token.

    A server is token-required when it has an env-var mapping (directly or via
    its inferred provider) — i.e. spawning it without a token guarantees a
    fatal crash. No-auth stdio servers (e.g. playwright; auth_provider
    "none") have no mapping and are excluded.
    """
    if config.get("auth_provider", "none") == "none":
        return False
    if server_name in _STDIO_TOKEN_ENV_VARS:
        return True
    return _infer_provider(server_name) in _STDIO_TOKEN_ENV_VARS


def _bearer_token(auth: BearerAuth | str | None) -> str | None:
    """Extract a non-empty bearer token string from a resolved auth, else None."""
    if not isinstance(auth, BearerAuth):
        return None
    token = auth.token
    raw = token.get_secret_value() if hasattr(token, "get_secret_value") else str(token)
    return raw or None


def _infer_provider(server_name: str) -> str:
    """Infer the OAuth provider from the MCP server name.

    Delegates to :mod:`src.integrations.provider_map` (the canonical map).
    """
    return provider_for_server(server_name)


def _inject_stdio_auth(config: dict, server_name: str, token: str) -> None:
    """Inject an OAuth/API token into the env dict for a stdio MCP server.

    Each server expects its token in a specific env var. Looks up the var
    name from _STDIO_TOKEN_ENV_VARS by server_name, falling back to
    _infer_provider(). Mutates config["env"] in place — caller must pass
    a copy, not the registered template.
    """
    env_var = _STDIO_TOKEN_ENV_VARS.get(server_name)
    if not env_var:
        provider = _infer_provider(server_name)
        env_var = _STDIO_TOKEN_ENV_VARS.get(provider)

    if not env_var:
        logger.debug(
            "No env var mapping for stdio server %s — auth token not injected",
            server_name,
        )
        return

    env = config.setdefault("env", {})
    env[env_var] = token
