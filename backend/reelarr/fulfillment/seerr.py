"""Overseerr / Jellyseerr fulfillment client (spec §5.5, "Via Seerr").

Seerr owns the Radarr/Sonarr wiring, root folders, quality profiles, and its
own approval queue — Reelarr only needs URL + API key and POST /api/v1/request.
"""

from __future__ import annotations

import httpx

from reelarr.fulfillment.base import (
    FulfillmentError,
    FulfillmentResult,
    FulfillmentStatus,
)
from reelarr.pipeline.tmdb import TmdbMatch


class SeerrClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=30.0)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key}

    async def test(self) -> dict:
        """Connectivity/auth/version only — Seerr enumerates nothing for us
        to live-populate (it owns its own downstream config)."""
        resp = await self._client.get(f"{self.base_url}/api/v1/status", headers=self._headers)
        resp.raise_for_status()
        status = resp.json()
        # /status is unauthenticated on Seerr; hit an authed endpoint to prove the key.
        auth = await self._client.get(f"{self.base_url}/api/v1/auth/me", headers=self._headers)
        auth.raise_for_status()
        return {"ok": True, "version": status.get("version")}

    async def fulfill(self, match: TmdbMatch) -> FulfillmentResult:
        payload: dict = {"mediaType": match.media_type, "mediaId": match.tmdb_id}
        if match.media_type == "tv":
            payload["seasons"] = "all"
        resp = await self._client.post(
            f"{self.base_url}/api/v1/request", headers=self._headers, json=payload
        )
        if resp.status_code == 409:
            # Same handling as Radarr/Sonarr's "already exists".
            return FulfillmentResult(
                FulfillmentStatus.ALREADY_EXISTS, "Already requested or available"
            )
        if resp.status_code >= 400:
            body = ""
            try:
                body = str(resp.json().get("message", ""))
            except ValueError:
                body = resp.text[:300]
            if "already" in body.lower():
                return FulfillmentResult(FulfillmentStatus.ALREADY_EXISTS, body)
            raise FulfillmentError(f"Seerr request failed ({resp.status_code}): {body}")
        return FulfillmentResult(
            FulfillmentStatus.ADDED, f"Requested {match.title} via Overseerr/Jellyseerr"
        )
