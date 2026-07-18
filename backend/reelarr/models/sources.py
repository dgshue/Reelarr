"""Source (intake channel) configuration + per-channel allowlists (spec §4)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from reelarr.db import Base


class SourceChannel(Base):
    __tablename__ = "source_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # telegram / discord / slack / whatsapp
    channel_type: Mapped[str] = mapped_column(String(32), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Channel-specific config: bot token, app token, Evolution instance name, etc.
    # NOTE: tokens live here (SQLite) rather than in compose files, per the
    # stack's secrets policy; env vars only seed initial values.
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    allowlist: Mapped[list["SourceAllowlistEntry"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class SourceAllowlistEntry(Base):
    """Who may talk to the bot on a given channel.

    Generalizes the original TELEGRAM_ALLOWED_CHAT_ID pattern: chat ID
    (Telegram), channel ID (Discord/Slack), phone number (WhatsApp), or a
    user ID — one row each.
    """

    __tablename__ = "source_allowlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("source_channels.id"))
    # chat_id / user_id / channel_id / phone_number
    ref_type: Mapped[str] = mapped_column(String(32))
    ref_value: Mapped[str] = mapped_column(String(128))
    label: Mapped[str | None] = mapped_column(String(128))  # friendly name shown in UI
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    channel: Mapped[SourceChannel] = relationship(back_populates="allowlist")
