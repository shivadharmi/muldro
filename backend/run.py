"""Entry point for running the Jarvis backend server."""

import uvicorn

from src.config.settings import get_settings


def main():
    settings = get_settings()
    uvicorn.run(
        "src.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
