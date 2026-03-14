from functools import lru_cache

import anthropic
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "JARVIS_", "env_file": ".env", "env_file_encoding": "utf-8"}

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

    # OpenClaw integration
    openclaw_gateway_url: str = "http://localhost:18789"
    openclaw_hook_token: str = ""

    # Embeddings (Voyage AI)
    voyage_api_key: str = ""
    voyage_base_url: str = "https://api.voyageai.com/v1"
    embedding_model: str = "voyage-3-lite"

    # Thresholds
    importance_threshold: float = 0.7  # Events above this score trigger planning
    briefing_lookback_hours: int = 24  # Default time window for briefing data

    # Security
    backend_token: str = ""  # Token for authenticating OpenClaw plugin calls
    rate_limit_rpm: int = 120  # Requests per minute per IP
    max_request_body_bytes: int = 1_048_576  # 1MB
    cors_allowed_origins: str = ""  # Comma-separated origins (empty = no CORS)

    # Retry policy
    retry_max_attempts: int = 3
    retry_base_delay: float = 1.0  # seconds
    retry_max_delay: float = 30.0  # seconds

    # Hardening
    plan_ttl_hours: int = 72  # Plans older than this are invalidated
    approval_ttl_hours: int = 24  # Approvals expire after this
    dlq_max_attempts: int = 3  # Dead-letter retry limit


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_anthropic_client(settings: Settings) -> anthropic.AsyncAnthropic:
    """Create the appropriate Anthropic client based on config."""
    if settings.use_bedrock:
        from anthropic import AsyncAnthropicBedrock

        return AsyncAnthropicBedrock(aws_region=settings.bedrock_region)  # type: ignore[return-value]
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
