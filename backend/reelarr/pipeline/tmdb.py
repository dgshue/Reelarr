"""TMDB client — /search/multi lookup + TVDB external-ID resolution for TV."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

TMDB_BASE = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w342"


@dataclass
class TmdbMatch:
    tmdb_id: int
    title: str
    year: int | None
    media_type: str  # "movie" | "tv"
    tvdb_id: int | None = None  # resolved lazily for TV (Sonarr needs it)
    poster_url: str | None = None
    overview: str | None = None


class TmdbClient:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def test(self) -> None:
        """Lightweight auth validation (Test button). Raises on failure."""
        resp = await self._client.get(
            f"{TMDB_BASE}/configuration", params={"api_key": self.api_key}
        )
        resp.raise_for_status()

    async def search_multi(self, query: str, year: int | None = None) -> list[TmdbMatch]:
        resp = await self._client.get(
            f"{TMDB_BASE}/search/multi",
            params={"api_key": self.api_key, "query": query, "include_adult": "false"},
        )
        resp.raise_for_status()
        matches: list[TmdbMatch] = []
        for item in resp.json().get("results", []):
            media_type = item.get("media_type")
            if media_type not in ("movie", "tv"):
                continue  # skip people etc.
            date = item.get("release_date") or item.get("first_air_date") or ""
            item_year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
            matches.append(
                TmdbMatch(
                    tmdb_id=item["id"],
                    title=item.get("title") or item.get("name") or "",
                    year=item_year,
                    media_type=media_type,
                    poster_url=f"{POSTER_BASE}{item['poster_path']}" if item.get("poster_path") else None,
                    overview=item.get("overview"),
                )
            )
        if year is not None:
            # Stable sort: matches for the identified year float to the top.
            matches.sort(key=lambda m: 0 if m.year == year else 1)
        return matches

    async def resolve_tvdb_id(self, tmdb_id: int) -> int | None:
        """TMDB external IDs endpoint — Sonarr requires tvdbId."""
        resp = await self._client.get(
            f"{TMDB_BASE}/tv/{tmdb_id}/external_ids", params={"api_key": self.api_key}
        )
        resp.raise_for_status()
        return resp.json().get("tvdb_id")

    async def aclose(self) -> None:
        await self._client.aclose()
