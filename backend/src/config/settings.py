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

    telegram_chat_id: str = ""  # Telegram chat ID for proactive message delivery

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

    # Telegram bot
    telegram_bot_token: str = ""  # Telegram Bot API token

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
    google_workspace_mcp_url: str = "http://localhost:8001/mcp"
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

    # Qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    # Neo4j
    neo4j_url: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # Registry validation
    skip_registry_validation: bool = False  # JARVIS_SKIP_REGISTRY_VALIDATION

    # Filesystem MCP server root directory (seeded into filesystem installation args).
    # Created at app startup if missing. Defaults to ~/jarvis-workspace.
    filesystem_mcp_root: str = ""

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
