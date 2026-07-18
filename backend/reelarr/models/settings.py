"""Settings persistence.

One row per Settings nav section (fulfillment / identification / sources /
connect / metadata / tags / general / ui). Each row holds a JSON payload that
is validated against the matching pydantic schema in
``reelarr.schemas.settings`` before being written. Values saved here override
the env-var defaults in ``reelarr.config``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from reelarr.db import Base


class SettingsSection(Base):
    __tablename__ = "settings_sections"

    # e.g. "fulfillment", "identification", "sources", "connect", "metadata",
    # "tags", "general", "ui"
    section: Mapped[str] = mapped_column(String(32), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
