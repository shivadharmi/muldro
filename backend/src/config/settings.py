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
    anthropic_model: str = "claude-sonnet-4-20250514"
    use_bedrock: bool = False
    bedrock_region: str = "ap-south-1"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    log_json: bool = False  # Use JSON structured logging

    telegram_chat_id: str = ""  # Telegram chat ID for proactive message delivery

    # Embeddings (Bedrock Titan)
    embedding_model: str = "amazon.titan-embed-text-v2:0"

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

    # Hardening
    plan_ttl_hours: int = 72  # Plans older than this are invalidated
    approval_ttl_hours: int = 24  # Approvals expire after this
    dlq_max_attempts: int = 3  # Dead-letter retry limit

    # Budget
    daily_token_budget_usd: float = 5.0  # Daily spend limit before degradation

    # Telegram bot
    telegram_bot_token: str = ""  # Telegram Bot API token

    # Auth
    session_secret_key: str = ""  # Secret for signing session tokens
    magic_link_ttl_minutes: int = 15
    session_ttl_hours: int = 720  # 30 days

    # Frontend
    frontend_url: str = "http://localhost:3000"

    # OAuth token encryption
    oauth_encryption_key: str = ""

    # OAuth providers
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/google/callback"
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_redirect_uri: str = "http://localhost:8000/v1/auth/github/callback"

    # S3 / artifact storage
    s3_bucket: str = ""
    s3_endpoint_url: str = ""  # For MinIO local dev
    s3_region: str = "ap-south-1"

    # Elasticsearch
    elasticsearch_url: str = ""

    # Qdrant
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    # Neo4j
    neo4j_url: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_anthropic_client(settings: Settings) -> anthropic.AsyncAnthropic:
    """Create the appropriate Anthropic client based on config."""
    if settings.use_bedrock:
        from anthropic import AsyncAnthropicBedrock

        return AsyncAnthropicBedrock(aws_region=settings.bedrock_region)  # type: ignore[return-value]
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
