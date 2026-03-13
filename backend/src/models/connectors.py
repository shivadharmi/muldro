from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class Connector(Base, TimestampMixin):
    __tablename__ = "connectors"

    connector_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # gmail, calendar, slack
    status: Mapped[str] = mapped_column(String(16), default="active")
    # active, paused, reauth_needed, error
    config: Mapped[dict | None] = mapped_column(JSONB)


class ConnectorAccount(Base, TimestampMixin):
    __tablename__ = "connector_accounts"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(256), nullable=False)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)  # encrypted OAuth tokens
    sync_cursor: Mapped[str | None] = mapped_column(String(512))  # last sync position
    sync_state: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="active")

    __table_args__ = (Index("ix_connector_accounts_connector", "connector_id"),)
