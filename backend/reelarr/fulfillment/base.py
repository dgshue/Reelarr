"""Common fulfillment interface (spec §5.5).

Two peer implementations, selected by Settings -> Fulfillment:
- DirectFulfillment  -> Radarr POST /api/v3/movie, Sonarr POST /api/v3/series
- SeerrClient        -> Overseerr/Jellyseerr POST /api/v1/request (approval-gated)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from reelarr.pipeline.tmdb import TmdbMatch


class FulfillmentStatus(str, enum.Enum):
    ADDED = "added"                  # newly added / requested
    ALREADY_EXISTS = "already_exists"  # in library / already requested / available


@dataclass
class FulfillmentResult:
    status: FulfillmentStatus
    detail: str = ""


class FulfillmentError(Exception):
    pass


@runtime_checkable
class FulfillmentClient(Protocol):
    async def fulfill(self, match: TmdbMatch) -> FulfillmentResult:
        """Add (or request) the identified media. Raises FulfillmentError on failure."""
        ...

    async def test(self) -> dict:
        """Test-button contract: validate connectivity/auth, return any
        live-populate payload (root folders, quality profiles, tags)."""
        ...
