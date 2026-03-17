"""Authentication service — magic links, OAuth, sessions."""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings
from src.models.ids import generate_user_id
from src.models.users import MagicLink, OAuthConnection, Session, User, Workspace, WorkspaceMember

logger = logging.getLogger(__name__)


class AuthService:
    """Manages authentication: magic links, OAuth, and sessions."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db

    async def send_magic_link(self, email: str) -> str:
        """Generate a magic link token and store it. Returns the raw token.

        In production, this would also send an email via SES/Resend.
        """
        token = secrets.token_urlsafe(48)
        token_hash = self._hash_token(token)

        link = MagicLink(
            link_id=f"ml_{ULID()}",
            email=email.lower().strip(),
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=self._settings.magic_link_ttl_minutes),
        )
        self._db.add(link)
        await self._db.commit()

        logger.info("Magic link created for %s", email)
        return token

    async def verify_magic_link(self, token: str) -> Session:
        """Verify a magic link token, create/find user, return session."""
        token_hash = self._hash_token(token)
        now = datetime.now(timezone.utc)

        result = await self._db.execute(
            select(MagicLink).where(
                MagicLink.token_hash == token_hash,
                MagicLink.used_at.is_(None),
                MagicLink.expires_at > now,
            )
        )
        link = result.scalar_one_or_none()
        if not link:
            raise ValueError("Invalid or expired magic link")

        link.used_at = now

        user = await self._get_or_create_user(link.email)
        session = await self._create_session(user.user_id, surface="web")

        await self._db.commit()
        logger.info("Magic link verified for %s, session created", link.email)
        return session

    async def initiate_oauth(self, provider: str, user_id: str | None = None) -> str:
        """Generate OAuth authorization URL. State encodes user_id if linking."""
        state = secrets.token_urlsafe(32)
        if user_id:
            state = f"{user_id}:{state}"
        # Store state in Redis for CSRF protection in a real implementation
        return state

    async def complete_oauth(
        self,
        provider: str,
        provider_user_id: str,
        email: str,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scopes: list[str] | None,
        user_id: str | None = None,
    ) -> Session:
        """Complete OAuth flow — create/link user, store tokens, return session."""
        if not user_id:
            user = await self._get_or_create_user(email)
            user_id = user.user_id

        await self._upsert_oauth_connection(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=scopes,
        )

        session = await self._create_session(user_id, surface="web")
        await self._db.commit()
        logger.info("OAuth %s completed for user %s", provider, user_id)
        return session

    async def validate_session(self, token: str) -> User | None:
        """Validate a session token. Returns the User or None.

        Sets user._workspace_id as a transient attribute from the session.
        """
        token_hash = self._hash_token(token)
        now = datetime.now(timezone.utc)

        result = await self._db.execute(
            select(Session).where(
                Session.token_hash == token_hash,
                Session.expires_at > now,
                Session.revoked_at.is_(None),
            )
        )
        session = result.scalar_one_or_none()
        if not session:
            return None

        result = await self._db.execute(
            select(User).where(User.user_id == session.user_id, User.status == "active")
        )
        user = result.scalar_one_or_none()
        if user:
            user._workspace_id = session.workspace_id  # type: ignore[attr-defined]
        return user

    async def refresh_session(self, refresh_token: str) -> Session:
        """Refresh an expired or active session. Returns a new session.

        The refresh_token is the raw token from the original session.
        The old session is revoked and a new one is created.
        """
        token_hash = self._hash_token(refresh_token)

        result = await self._db.execute(
            select(Session).where(
                Session.token_hash == token_hash,
                Session.revoked_at.is_(None),
            )
        )
        old_session = result.scalar_one_or_none()
        if not old_session:
            raise ValueError("Invalid or revoked session token")

        # Check the user is still active
        result = await self._db.execute(
            select(User).where(User.user_id == old_session.user_id, User.status == "active")
        )
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("User account is inactive")

        # Revoke old session
        old_session.revoked_at = datetime.now(timezone.utc)

        # Create new session
        new_session = await self._create_session(
            user_id=user.user_id, surface=old_session.surface or "web"
        )
        await self._db.commit()
        logger.info("Session refreshed for user %s", user.user_id)
        return new_session

    async def revoke_session(self, session_id: str) -> None:
        """Revoke a session."""
        result = await self._db.execute(select(Session).where(Session.session_id == session_id))
        session = result.scalar_one_or_none()
        if session:
            session.revoked_at = datetime.now(timezone.utc)
            await self._db.commit()

    async def get_user(self, user_id: str) -> User | None:
        """Get a user by ID."""
        result = await self._db.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    async def _get_or_create_user(self, email: str) -> User:
        """Find user by email or create a new one with default workspace."""
        email = email.lower().strip()
        result = await self._db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            return user

        user_id = generate_user_id()
        user = User(
            user_id=user_id,
            email=email,
            display_name=email.split("@")[0],
            status="active",
            settings={
                "policy_mode": "approval_required",
                "daily_budget_usd": 5.0,
                "notification_channels": ["web"],
            },
        )
        self._db.add(user)
        await self._db.flush()

        # Auto-create workspace
        ws_id = f"ws_{ULID()}"
        workspace = Workspace(
            workspace_id=ws_id,
            name=f"{user.display_name}'s Workspace",
            owner_user_id=user_id,
        )
        self._db.add(workspace)
        self._db.add(
            WorkspaceMember(
                workspace_id=ws_id,
                user_id=user_id,
                role="owner",
                joined_at=datetime.now(timezone.utc),
            )
        )
        logger.info("User created: %s (%s)", user_id, email)
        return user

    async def _create_session(self, user_id: str, surface: str = "web") -> Session:
        """Create a new session for a user. Returns the session with raw token."""
        raw_token = secrets.token_urlsafe(48)
        token_hash = self._hash_token(raw_token)

        # Resolve workspace_id — every user has exactly one owner workspace
        ws_result = await self._db.execute(
            select(WorkspaceMember.workspace_id).where(
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.role == "owner",
            )
        )
        workspace_id = ws_result.scalar_one_or_none()

        session = Session(
            session_id=f"sess_{ULID()}",
            user_id=user_id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=self._settings.session_ttl_hours),
            surface=surface,
            workspace_id=workspace_id,
        )
        self._db.add(session)
        # Store raw token as transient attribute for the caller
        session._raw_token = raw_token  # type: ignore[attr-defined]
        return session

    async def _upsert_oauth_connection(
        self,
        user_id: str,
        provider: str,
        provider_user_id: str,
        email: str,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scopes: list[str] | None,
    ) -> None:
        """Create or update an OAuth connection."""
        result = await self._db.execute(
            select(OAuthConnection).where(
                OAuthConnection.user_id == user_id,
                OAuthConnection.provider == provider,
            )
        )
        conn = result.scalar_one_or_none()

        encrypted_access = self._encrypt_token(access_token)
        encrypted_refresh = self._encrypt_token(refresh_token) if refresh_token else None

        if conn:
            conn.provider_user_id = provider_user_id
            conn.email = email
            conn.access_token_encrypted = encrypted_access
            conn.refresh_token_encrypted = encrypted_refresh
            conn.expires_at = expires_at
            conn.scopes = scopes
        else:
            conn = OAuthConnection(
                connection_id=f"oac_{ULID()}",
                user_id=user_id,
                provider=provider,
                provider_user_id=provider_user_id,
                email=email,
                access_token_encrypted=encrypted_access,
                refresh_token_encrypted=encrypted_refresh,
                expires_at=expires_at,
                scopes=scopes,
            )
            self._db.add(conn)

    def _encrypt_token(self, plaintext: str) -> str:
        """Encrypt a token using Fernet if an encryption key is configured."""
        key = self._settings.oauth_encryption_key
        if not key:
            logger.warning("No oauth_encryption_key set — storing token as-is")
            return plaintext
        try:
            from cryptography.fernet import Fernet

            f = Fernet(key.encode() if isinstance(key, str) else key)
            return f.encrypt(plaintext.encode()).decode()
        except Exception:
            logger.warning("Fernet encryption failed, storing token as-is", exc_info=True)
            return plaintext

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
