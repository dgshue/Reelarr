"""Request / queue / history / blocklist records.

Maps to the UI nav (spec §1):
- Activity → Queue      = MediaRequest rows in an in-flight status
- Activity → History    = MediaRequest rows in a terminal status
- Activity → Blocklist  = BlocklistEntry
- Pending Confirmation  = MediaRequest rows in PENDING_CONFIRMATION
- Library               = MediaRequest rows that were fulfilled (poster grid)
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from reelarr.db import Base


class RequestStatus(str, enum.Enum):
    # In-flight (Activity → Queue)
    QUEUED = "queued"
    IDENTIFYING = "identifying"
    # Awaiting a user reply on the originating channel (Pending Confirmation)
    PENDING_CONFIRMATION = "pending_confirmation"
    # Identification confirmed, fulfillment in progress
    FULFILLING = "fulfilling"
    # Terminal (Activity → History)
    FULFILLED = "fulfilled"          # added to Radarr/Sonarr or requested via Seerr
    ALREADY_EXISTS = "already_exists"
    FAILED = "failed"
    DISMISSED = "dismissed"          # user picked "None of these" / dismissed


class MediaRequest(Base):
    __tablename__ = "media_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(32))  # instagram/tiktok/facebook

    # Where it came from (IntakeChannel)
    source_channel: Mapped[str] = mapped_column(String(32))  # telegram/discord/slack/whatsapp
    source_chat_ref: Mapped[str] = mapped_column(String(128))  # chat/channel/number id
    source_user_ref: Mapped[str | None] = mapped_column(String(128))
    source_message_ref: Mapped[str | None] = mapped_column(String(128))

    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, native_enum=False, length=32), default=RequestStatus.QUEUED
    )
    # Which pipeline tier resolved it: metadata / transcript / frames
    resolved_tier: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[str | None] = mapped_column(String(8))  # high/medium/low

    # Identified media
    title: Mapped[str | None] = mapped_column(String(512))
    year: Mapped[int | None] = mapped_column(Integer)
    media_type: Mapped[str | None] = mapped_column(String(8))  # movie/tv
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    tvdb_id: Mapped[int | None] = mapped_column(Integer)
    poster_url: Mapped[str | None] = mapped_column(Text)
    overview: Mapped[str | None] = mapped_column(Text)

    # Candidates offered for confirmation (top-3 TMDB matches, JSON list)
    candidates: Mapped[list | None] = mapped_column(JSON)

    fulfillment_target: Mapped[str | None] = mapped_column(String(16))  # direct/seerr
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    events: Mapped[list["RequestEvent"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )


class RequestEvent(Base):
    """Structured per-request activity log (System → Events feed)."""

    __tablename__ = "request_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("media_requests.id"))
    event_type: Mapped[str] = mapped_column(String(64))  # e.g. tier1_result, tmdb_lookup, fulfilled
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    request: Mapped[MediaRequest] = relationship(back_populates="events")


class BlocklistEntry(Base):
    """Dismissed links — resubmitting one of these is ignored with a notice."""

    __tablename__ = "blocklist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    reason: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
