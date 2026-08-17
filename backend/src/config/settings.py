from functools import lru_cache

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

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    environment: str = "development"  # Environment discriminator (development, staging, production)
    log_json: bool = False  # Use JSON structured logging

    # Embeddings — local fastembed (ONNX, no external API). Model determines the vector
    # dimension; keep it in sync with vector_store.VECTOR_SIZE (bge-base-en-v1.5 = 768).
    embedding_model: str = "BAAI/bge-base-en-v1.5"

    # Reranker — local fastembed cross-encoder (ONNX, no external API).
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-12-v2"
    reranker_enabled: bool = True

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

    # Step 10D A-5: gate the deep-chat single-lead restructure. When True AND mode=="ask",
    # the chat path runs ONE synthetic lead over the whole goal (built in 5a, wired in 5b)
    # instead of the per-step loop + presenter step.
    # Off by default — dormant until the 5b chat wiring lands and this flag is flipped.
    deep_single_lead: bool = False  # JARVIS_DEEP_SINGLE_LEAD

    # Step 10D P2.5c: drop the Planner from the deep chat single-lead path. When True (and only
    # when the single-lead path is already active — deep_single_lead + permission_mode), a chat
    # turn skips classify_intent + fast-path + Planner + plan record +
    # resolve_plan_routing entirely and builds ONE lead from the connector-derived scope
    # (resolve_connector_scope). Off by default — dormant; flag-off is byte-identical (Planner
    # still called). Gates ONLY the P2.5c reroute, independently of deep_single_lead.
    chat_planless: bool = False  # JARVIS_CHAT_PLANLESS

    # Step-10A A3: opt-in fail-closed write lock. When True, a WRITE tool call is REFUSED
    # (not executed unlocked) if Redis is unreachable — for prod where Redis is expected up.
    # Default False preserves today's fail-OPEN behavior (authz is still enforced by
    # capability_scope + trust_gate; autonomous double-fire is still guarded by the
    # idempotency ledger the lock wraps). Applies to BOTH the deep middleware and the
    # autonomous wrapper.
    write_lock_require_redis: bool = False  # JARVIS_WRITE_LOCK_REQUIRE_REDIS

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

    # OpenConnector gateway. Not optional: the migrated installations
    # (google-workspace, github) have no native transport to fall back to, so a
    # deployment without a reachable vMCP simply loses those integrations —
    # loudly, at session-open time. There is deliberately no feature flag.
    toolhive_vmcp_url: str | None = None  # JARVIS_TOOLHIVE_VMCP_URL
    openconnector_mcp_url: str | None = None  # JARVIS_OPENCONNECTOR_MCP_URL
    openconnector_runtime_token: str | None = None  # JARVIS_OPENCONNECTOR_RUNTIME_TOKEN
    openconnector_admin_url: str | None = None  # JARVIS_OPENCONNECTOR_ADMIN_URL
    openconnector_admin_token: str | None = None  # JARVIS_OPENCONNECTOR_ADMIN_TOKEN
    platform_jwt_private_pem: str | None = None  # JARVIS_PLATFORM_JWT_PRIVATE_PEM

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
        """Return the configured direct-Anthropic model ID."""
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
        if not self.anthropic_api_key:
            raise RuntimeError(
                "JARVIS_ANTHROPIC_API_KEY is not set. Jarvis cannot talk to any agent "
                "without it. Set it in your .env (get a key at https://console.anthropic.com)."
            )

        if self.is_production and not self.oauth_encryption_key:
            raise RuntimeError(
                "JARVIS_OAUTH_ENCRYPTION_KEY is required in production — without it, "
                "OAuth tokens would be stored as plaintext. Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
