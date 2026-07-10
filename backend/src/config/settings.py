from functools import lru_cache

import anthropic
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "JARVIS_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # Database
    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    redis_url: str = "redis://localhost:6379/0"

    # Database connection self-protection (asyncpg server_settings, milliseconds).
    # Every connection bounds idle-in-transaction and statement duration so a
    # leaked transaction (e.g. a stuck perception tick holding a row lock) cannot
    # freeze the worker indefinitely — a durable, env-agnostic backstop.
    #
    # The idle ceiling MUST exceed the longest legitimate idle-in-transaction
    # window. GraphExecutor holds ONE long-lived session across an entire DAG;
    # between per-step flushes that connection sits idle-in-transaction while the
    # agent loop runs (on separate sessions). A single step can take up to the
    # step timeout (120s) and a background run is capped at 600s, so a 60s ceiling
    # would terminate the executor's connection mid-run — the reaper would then
    # re-drive → re-kill → DLQ. 900s (15 min) sits safely above the 600s run cap.
    # Settings-overridable. statement_timeout stays at 120s (single statements
    # are always short; only the transaction-level idle window is long).
    db_idle_in_transaction_timeout_ms: int = 900_000
    db_statement_timeout_ms: int = 120_000

    # Scheduler resilience knobs.
    # Per-sub-tick timeout: a single hung sub-tick (e.g. perception holding a
    # lock) must never starve later sub-ticks (resume / health). On timeout the
    # dispatcher logs and continues to the next sub-tick.
    scheduler_subtick_timeout_s: float = 90.0
    # Stale approval-resume reaper: a run approved by the user but never resumed
    # by the background tick is re-driven through resume_run after this age, and
    # failed after the attempt cap to avoid hot-looping.
    resume_reaper_stale_after_s: float = 300.0
    resume_reaper_max_attempts: int = 5
    # Max stale runs re-driven per reaper pass. Bounded + SELECT … FOR UPDATE
    # SKIP LOCKED so (a) two schedulers can never double-drive the same run and
    # (b) an unbounded batch cannot starve the 90s sub-tick timeout. The gauge
    # count (in _update_loop_gauges) stays unbounded — it reports the full backlog.
    resume_reaper_batch_limit: int = 5

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    use_bedrock: bool = False
    bedrock_region: str = "us-east-1"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    environment: str = "development"  # Environment discriminator (development, staging, production)
    log_json: bool = False  # Use JSON structured logging

    # Embeddings — Voyage AI (primary) or Bedrock Titan (fallback when no voyage_api_key)
    embedding_model: str = "voyage-3"
    voyage_api_key: str = ""
    voyage_base_url: str = "https://api.voyageai.com/v1"

    # Reranker (Bedrock) — available in: us-west-2, eu-central-1, ap-northeast-1, ca-central-1
    reranker_model: str = "amazon.rerank-v1:0"
    reranker_enabled: bool = True
    reranker_region: str = "us-west-2"

    # Thresholds
    importance_threshold: float = 0.7  # Events above this score trigger planning
    briefing_lookback_hours: int = 24  # Default time window for briefing data

    # Security
    backend_token: str = ""  # Token for authenticating API calls
    rate_limit_rpm: int = 120  # Requests per minute per IP
    max_request_body_bytes: int = 1_048_576  # 1MB
    cors_allowed_origins: str = ""  # Comma-separated origins (empty = no CORS)

    # Retry policy
    retry_max_attempts: int = 3
    retry_base_delay: float = 1.0  # seconds
    retry_max_delay: float = 30.0  # seconds

    # Observation stale thresholds
    observation_stale_gmail_minutes: int = 30
    observation_stale_calendar_minutes: int = 180
    observation_stale_github_minutes: int = 60
    observation_stale_notion_minutes: int = 60

    # Hardening
    plan_ttl_hours: int = 72  # Plans older than this are invalidated
    approval_ttl_hours: int = 24  # Approvals expire after this
    dlq_max_attempts: int = 3  # Dead-letter retry limit

    # Budget
    daily_token_budget_usd: float = 25.0  # Daily spend limit before degradation
    cheap_mode: bool = False  # All-Sonnet preset (no Opus) + halved thinking budgets

    # Backpressure
    event_processor_concurrency: int = 5  # Max concurrent event scoring calls
    max_perception_per_tick: int = 5  # Max perception cycles per scheduler tick
    webhook_lag_threshold: int = 5000  # Reject webhooks when stream lag exceeds this

    # Auth
    magic_link_ttl_minutes: int = 15
    session_ttl_hours: int = 720  # 30 days

    # SES Email
    ses_from_address: str = ""  # e.g. "jarvis@yourdomain.com"
    ses_region: str = "ap-south-1"
    ses_enabled: bool = False  # Must be explicitly enabled for production

    # Frontend
    frontend_url: str = "http://localhost:3000"

    # OAuth token encryption
    oauth_encryption_key: str = ""

    # OAuth providers
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/google/callback"
    # Google Workspace MCP now runs as an on-demand local uvx process
    # (LocalMCPProcessManager), so there is no static URL. These knobs tune
    # the local-process lifecycle and the idle-session reaper.
    mcp_local_ready_timeout_s: float = 30.0
    mcp_session_idle_ttl_s: float = 120.0
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/github/callback"

    # Notion OAuth
    notion_oauth_client_id: str = ""
    notion_oauth_client_secret: str = ""
    notion_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/notion/callback"
    notion_token: str = ""  # For MCP server (@notionhq/notion-mcp-server)

    # Atlassian OAuth (Jira + Confluence via Rovo MCP)
    atlassian_oauth_client_id: str = ""
    atlassian_oauth_client_secret: str = ""
    atlassian_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/atlassian/callback"

    # S3 / artifact storage
    s3_bucket: str = ""
    s3_endpoint_url: str = ""  # For MinIO local dev
    s3_region: str = "ap-south-1"
    # Explicit credentials for MinIO / local dev. Leave empty in production so
    # the default AWS credential chain (IAM role / instance profile) is used.
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    # Qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    # Neo4j
    neo4j_url: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # Registry validation
    skip_registry_validation: bool = False  # JARVIS_SKIP_REGISTRY_VALIDATION

    # Chat execution runtime: "legacy" (agent_loop) | "deep" (Deep Agents lead).
    # Default legacy so the Deep Agents path is dormant until explicitly enabled.
    runtime: str = "legacy"  # JARVIS_RUNTIME

    # Deep-only inline-format augmentation (Step 7B1 P4, Fork-1): when True, the deep
    # lead's system prompt is augmented with PRESENTER_VOICE so it formats the
    # user-facing reply inline (chat reply + optional fenced surface spec) instead of
    # delegating to a separate Presenter step. Off by default and legacy-untouched —
    # live activation (dropping the separate presenter step) is a Step-10 gate.
    deep_inline_format: bool = False  # JARVIS_DEEP_INLINE_FORMAT

    # Deep delegate layer (Step 7B2): when True, the deep lead is built with
    # read-only Jarvis sub-agents (e.g. Perceiver) registered via
    # ``create_deep_agent(subagents=)`` so it can route reads through the built-in
    # ``task`` tool. Off by default — the layer stays dormant (no live lead→delegate
    # routing) until explicitly enabled; live wiring is a Step-8/10 gate.
    deep_delegates_enabled: bool = False  # JARVIS_DEEP_DELEGATES_ENABLED

    # Deep inline read-back verifier (Step 7C): when True, an inner-of-write_lock
    # ``@wrap_tool_call`` middleware reads an irreversible/external write's effect back
    # and ANNOTATES the verdict onto the ToolMessage content (never status, so the SSE
    # frame does not flip to blocked). Off by default — dormant until Phase 3 wires it
    # into the chain; live activation is a Step-10 gate.
    deep_readback_enabled: bool = False  # JARVIS_DEEP_READBACK_ENABLED

    # Step 8: gate the JIT-hybrid slim context pack. Deep chat path only; when
    # False the deep path builds the full eager pack (byte-identical to legacy).
    deep_context_jit: bool = False  # JARVIS_DEEP_CONTEXT_JIT

    # Step 10B Task 3b: fraction of chat turns (0.0-1.0) that ALSO run a
    # NON-authoritative shadow turn on the opposite runtime, for the cutover
    # control-plane's shadow-compare harness (ShadowRunner). Default 0.0 = off —
    # the chat seam never even schedules the background shadow task, and
    # ShadowRunner.maybe_run_shadow's sampling check returns immediately either
    # way. Live activation (a nonzero rate) is a Step-10 gate.
    shadow_sample_rate: float = 0.0  # JARVIS_SHADOW_SAMPLE_RATE

    # Step-10A A3: opt-in fail-closed write lock. When True, a WRITE tool call is REFUSED
    # (not executed unlocked) if Redis is unreachable — for prod where Redis is expected up.
    # Default False preserves today's fail-OPEN behavior (authz is still enforced by
    # capability_scope + trust_gate; autonomous double-fire is still guarded by the
    # idempotency ledger the lock wraps). Applies to BOTH the deep middleware and the
    # autonomous wrapper.
    write_lock_require_redis: bool = False  # JARVIS_WRITE_LOCK_REQUIRE_REDIS

    # Step 10B Task 5a: auto-rollback watcher per-signal breach thresholds. Each is
    # the minimum per-TICK DELTA (not cumulative count) of the mapped rollback-gate
    # signal (see metrics_service.py) that trips a currently-"deep" surface's breaker
    # back to "legacy" (src/services/scheduler/runtime_rollback_tick.py). Conservative
    # defaults — the watcher is dormant machinery until a surface's enable key flips it
    # to "deep"; live tuning is a Step-10D gate.
    rollback_double_fire_threshold: int = 5  # JARVIS_ROLLBACK_DOUBLE_FIRE_THRESHOLD
    rollback_verification_false_negative_threshold: int = 3
    rollback_double_prompt_threshold: int = 3
    rollback_ungated_perception_write_threshold: int = 1
    rollback_shadow_divergence_threshold: int = 10

    # Step 10B Task 5b: operator credential for the runtime kill-switch admin route
    # (POST/DELETE /v1/admin/runtime/override). Default empty → the admin route is
    # DISABLED (fail-closed): every request is rejected 403 until ops sets
    # JARVIS_ADMIN_API_TOKEN. The escape hatch forces the SAFE direction only (legacy);
    # it is compared against the X-Admin-Token header in constant time (hmac.compare_digest).
    admin_api_token: str = ""  # JARVIS_ADMIN_API_TOKEN

    # Webhook / push-notification infrastructure (OPTIONAL — empty = poll-only).
    # When unset, webhook registration is a graceful no-op and the system stays
    # poll-only exactly as before. All three must be satisfied (see
    # ``webhooks_configured``) for any provider channel to be created.
    webhooks_enabled: bool = False  # master switch (JARVIS_WEBHOOKS_ENABLED)
    # Public HTTPS base, e.g. "https://jarvis.example.com". The full provider
    # callback is "{base}/v1/webhooks/{provider}/{subscription_id}".
    webhook_callback_base_url: str = ""  # JARVIS_WEBHOOK_CALLBACK_BASE_URL
    # Full Pub/Sub topic name "projects/{proj}/topics/{topic}" for Gmail users.watch.
    gmail_pubsub_topic: str = ""  # JARVIS_GMAIL_PUBSUB_TOPIC

    @property
    def webhooks_configured(self) -> bool:
        """True only when push registration can actually create provider channels.

        Requires the master switch AND a public callback base URL. When False,
        ``WebhookManager.register`` short-circuits to a no-op and every source
        stays in poll mode — the default, infra-free behavior.
        """
        return bool(self.webhooks_enabled and self.webhook_callback_base_url)

    @property
    def resolved_model(self) -> str:
        """Return the model ID appropriate for the configured backend (direct API or Bedrock)."""
        if self.use_bedrock:
            return _to_bedrock_model_id(self.anthropic_model)
        return self.anthropic_model

    @property
    def is_production(self) -> bool:
        """True when running under the production environment discriminator."""
        return self.environment == "production"

    def validate_startup(self) -> None:
        """Fail fast on misconfiguration that would otherwise surface as a cryptic
        runtime error (empty API key → opaque first-chat failure) or a silent
        security downgrade (no OAuth key → tokens stored as plaintext).

        Raises RuntimeError with an actionable message. Called once at app startup.
        """
        if not self.use_bedrock and not self.anthropic_api_key:
            raise RuntimeError(
                "JARVIS_ANTHROPIC_API_KEY is not set. Jarvis cannot talk to any agent "
                "without it. Set it in your .env (get a key at https://console.anthropic.com), "
                "or set JARVIS_USE_BEDROCK=true to use AWS Bedrock credentials instead."
            )

        if self.is_production and not self.oauth_encryption_key:
            raise RuntimeError(
                "JARVIS_OAUTH_ENCRYPTION_KEY is required in production — without it, "
                "OAuth tokens would be stored as plaintext. Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )


# Mapping from direct API model IDs to Bedrock inference profile IDs
# Uses cross-region profiles (apac/global) that work in ap-south-1
_BEDROCK_MODEL_MAP = {
    # Claude 4 (legacy)
    "claude-opus-4-20250514": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    "claude-sonnet-4-20250514": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "claude-haiku-4-20250514": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    # Claude 4.5 (legacy)
    "claude-sonnet-4-5-20250929": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude-haiku-4-5-20251001": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-opus-4-5-20251101": "us.anthropic.claude-opus-4-5-20251101-v1:0",
    # Claude 4.6 / 4.8 (latest)
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
    "claude-opus-4-8": "us.anthropic.claude-opus-4-8",
}


def _to_bedrock_model_id(model: str) -> str:
    """Convert a direct API model ID to its Bedrock equivalent."""
    if model in _BEDROCK_MODEL_MAP:
        return _BEDROCK_MODEL_MAP[model]
    # Already a Bedrock model ID
    if model.startswith("anthropic."):
        return model
    # Fallback: wrap with Bedrock convention
    return f"anthropic.{model}-v1:0"


@lru_cache
def get_settings() -> Settings:
    return Settings()


_anthropic_client: anthropic.AsyncAnthropic | None = None


def get_anthropic_client(settings: Settings) -> anthropic.AsyncAnthropic:
    """Return a shared Anthropic client (singleton).

    Reusing a single client avoids leaking aiohttp/httpx sessions that each
    new AsyncAnthropic() instance creates internally.
    """
    global _anthropic_client
    if _anthropic_client is None:
        if settings.use_bedrock:
            from anthropic import AsyncAnthropicBedrock

            _anthropic_client = AsyncAnthropicBedrock(aws_region=settings.bedrock_region)  # type: ignore[assignment]
        else:
            _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


async def close_anthropic_client() -> None:
    """Close the shared Anthropic client. Call at app shutdown."""
    global _anthropic_client
    if _anthropic_client is not None:
        await _anthropic_client.close()
        _anthropic_client = None
