from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "JARVIS_"}

    # Database
    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    redis_url: str = "redis://localhost:6379/0"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # OpenClaw integration
    openclaw_gateway_url: str = "http://localhost:18789"
    openclaw_hook_token: str = ""

    # Thresholds
    importance_threshold: float = 0.7  # Events above this score trigger planning
    briefing_lookback_hours: int = 24  # Default time window for briefing data

    # Security
    backend_token: str = ""  # Token for authenticating OpenClaw plugin calls


@lru_cache
def get_settings() -> Settings:
    return Settings()
