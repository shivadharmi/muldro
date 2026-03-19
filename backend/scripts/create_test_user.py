"""Create a test user and session token for local development.

Usage:
    cd backend
    python -m scripts.create_test_user
    python -m scripts.create_test_user --email user@example.com
"""

import argparse
import asyncio
import sys

from src.config.settings import get_settings
from src.models.database import get_session_factory
from src.services.auth_service import AuthService


async def main(email: str) -> None:
    settings = get_settings()
    factory = get_session_factory()

    async with factory() as db:
        auth = AuthService(settings, db)

        # Generate magic link token
        token = await auth.send_magic_link(email)

        # Immediately verify it to create user + session
        session = await auth.verify_magic_link(token)
        raw_token = session._raw_token  # type: ignore[attr-defined]

        user = await auth.get_user(session.user_id)
        await db.commit()

    print(f"\nTest user created successfully!")
    print(f"  User ID:       {user.user_id}")
    print(f"  Email:         {user.email}")
    print(f"  Display Name:  {user.display_name}")
    print(f"  Workspace ID:  {session.workspace_id}")
    print(f"  Session ID:    {session.session_id}")
    print(f"  Expires:       {session.expires_at.isoformat()}")
    print(f"\n  Session Token (use as Authorization: Bearer <token>):")
    print(f"  {raw_token}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a test user for Jarvis")
    parser.add_argument("--email", default="admin@jarvis.local", help="User email")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.email))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
