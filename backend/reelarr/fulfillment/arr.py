"""Radarr / Sonarr direct clients (API v3) + the Direct fulfillment target."""

from __future__ import annotations

import httpx

from reelarr.fulfillment.base import (
    FulfillmentError,
    FulfillmentResult,
    FulfillmentStatus,
)
from reelarr.pipeline.tmdb import TmdbMatch


class _ArrClient:
    """Shared plumbing for Radarr/Sonarr (same API idiom)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        root_folder: str = "",
        quality_profile_id: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.root_folder = root_folder
        self.quality_profile_id = quality_profile_id
        self._client = client or httpx.AsyncClient(timeout=30.0)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key}

    async def _get(self, path: str) -> httpx.Response:
        resp = await self._client.get(f"{self.base_url}/api/v3{path}", headers=self._headers)
        resp.raise_for_status()
        return resp

    async def test(self) -> dict:
        """Seerr-style Test: validate connectivity/auth/version AND return the
        data that live-populates the form's dropdowns (root folders, quality
        profiles, tags) in one round trip."""
        status = (await self._get("/system/status")).json()
        root_folders = (await self._get("/rootfolder")).json()
        profiles = (await self._get("/qualityprofile")).json()
        tags = (await self._get("/tag")).json()
        return {
            "ok": True,
            "version": status.get("version"),
            "rootFolders": [{"id": r["id"], "path": r["path"]} for r in root_folders],
            "qualityProfiles": [{"id": p["id"], "name": p["name"]} for p in profiles],
            "tags": [{"id": t["id"], "label": t["label"]} for t in tags],
        }

    @staticmethod
    def _is_already_exists(resp: httpx.Response) -> bool:
        """Radarr/Sonarr return 400 with a validation message when the item
        is already in the library."""
        if resp.status_code != 400:
            return False
        try:
            body = resp.json()
        except ValueError:
            return False
        errors = body if isinstance(body, list) else [body]
        for e in errors:
            code = str(e.get("errorCode", "")) if isinstance(e, dict) else ""
            msg = str(e.get("errorMessage", "")) if isinstance(e, dict) else str(e)
            if "exist" in msg.lower() or "Exists" in code:
                return True
        return False


class RadarrClient(_ArrClient):
    async def add_movie(self, tmdb_id: int, title: str, year: int | None) -> FulfillmentResult:
        payload = {
            "tmdbId": tmdb_id,
            "title": title,
            "year": year,
            "qualityProfileId": self.quality_profile_id,
            "rootFolderPath": self.root_folder,
            "monitored": True,
            "addOptions": {"searchForMovie": True},
        }
        resp = await self._client.post(
            f"{self.base_url}/api/v3/movie", headers=self._headers, json=payload
        )
        if self._is_already_exists(resp):
            return FulfillmentResult(FulfillmentStatus.ALREADY_EXISTS, "Already in your library")
        if resp.status_code >= 400:
            raise FulfillmentError(f"Radarr add failed ({resp.status_code}): {resp.text[:300]}")
        return FulfillmentResult(FulfillmentStatus.ADDED, f"Added {title} to Radarr")


class SonarrClient(_ArrClient):
    async def add_series(self, tvdb_id: int, title: str) -> FulfillmentResult:
        payload = {
            "tvdbId": tvdb_id,
            "title": title,
            "qualityProfileId": self.quality_profile_id,
            "rootFolderPath": self.root_folder,
            "monitored": True,
            "addOptions": {"searchForMissingEpisodes": True},
        }
        resp = await self._client.post(
            f"{self.base_url}/api/v3/series", headers=self._headers, json=payload
        )
        if self._is_already_exists(resp):
            return FulfillmentResult(FulfillmentStatus.ALREADY_EXISTS, "Already in your library")
        if resp.status_code >= 400:
            raise FulfillmentError(f"Sonarr add failed ({resp.status_code}): {resp.text[:300]}")
        return FulfillmentResult(FulfillmentStatus.ADDED, f"Added {title} to Sonarr")


class DirectFulfillment:
    """FulfillmentClient that routes movie->Radarr, tv->Sonarr."""

    def __init__(self, radarr: RadarrClient, sonarr: SonarrClient) -> None:
        self.radarr = radarr
        self.sonarr = sonarr

    async def fulfill(self, match: TmdbMatch) -> FulfillmentResult:
        if match.media_type == "movie":
            return await self.radarr.add_movie(match.tmdb_id, match.title, match.year)
        if match.media_type == "tv":
            if not match.tvdb_id:
                raise FulfillmentError(
                    f"No tvdbId resolved for '{match.title}' — Sonarr requires one"
                )
            return await self.sonarr.add_series(match.tvdb_id, match.title)
        raise FulfillmentError(f"Unsupported media type: {match.media_type}")

    async def test(self) -> dict:
        return {"radarr": await self.radarr.test(), "sonarr": await self.sonarr.test()}
