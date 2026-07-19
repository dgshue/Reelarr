"""TMDB client — /search/multi lookup + TVDB external-ID resolution for TV."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

TMDB_BASE = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w342"

# The LLM's year is frequently off by a year or two even when the title is
# right (streaming vs theatrical dates, plain hallucination). Within this
# window a mismatch is treated as drift, not as evidence of a different film.
YEAR_DRIFT_TOLERANCE = 2


@dataclass
class TmdbMatch:
    tmdb_id: int
    title: str
    year: int | None
    media_type: str  # "movie" | "tv"
    tvdb_id: int | None = None  # resolved lazily for TV (Sonarr needs it)
    poster_url: str | None = None
    overview: str | None = None
    popularity: float = 0.0   # TMDB popularity — ranking tiebreak only
    vote_count: int = 0


def normalize_title(title: str) -> str:
    """Casefold + collapse whitespace — the single definition of 'same title'."""
    return re.sub(r"\s+", " ", title.strip().casefold())


def _title_similarity(query: str, title: str) -> float:
    """Token overlap in [0, 1]; 1.0 iff the normalized titles are identical.

    Deliberately crude — it only has to keep near-titles ("The Gorge 2")
    ahead of loose matches ("Enormous: The Gorge Story") among NON-exact
    candidates. Exact matches are ranked by the boolean above it.
    """
    q = set(re.split(r"[^\w]+", normalize_title(query))) - {""}
    t = set(re.split(r"[^\w]+", normalize_title(title))) - {""}
    if not q or not t:
        return 0.0
    return len(q & t) / max(len(q), len(t))


def _year_bucket(ident_year: int | None, match_year: int | None) -> int:
    """Year proximity as a coarse bucket, NOT a filter (the identified year is
    frequently hallucinated — measured live: 'The Gorge (2025)' identified as
    2023). Exact is best, ±1-2 is near-free (release-date vs streaming-date
    drift), unknown beats a distant year, a large gap is a strong demotion but
    never disqualifying."""
    if ident_year is None or match_year is None:
        return 2
    gap = abs(ident_year - match_year)
    if gap == 0:
        return 4
    if gap <= YEAR_DRIFT_TOLERANCE:
        return 3
    if gap <= 5:
        return 1
    return 0


def rank_matches(query: str, year: int | None, matches: list[TmdbMatch]) -> list[TmdbMatch]:
    """Order candidates by evidence quality: exact title match dominates
    everything, then title similarity, then year proximity, then popularity/
    vote count. Replaces the old exact-year float, which let junk like
    'The Corpse in the Gorge (2023)' outrank 'The Gorge (2025)' whenever the
    LLM hallucinated the year."""
    wanted = normalize_title(query)

    def key(m: TmdbMatch) -> tuple:
        return (
            normalize_title(m.title) == wanted,
            round(_title_similarity(query, m.title), 3),
            _year_bucket(year, m.year),
            m.popularity,
            m.vote_count,
        )

    return sorted(matches, key=key, reverse=True)  # stable: TMDB order breaks ties


@dataclass
class PersonCredit:
    """One acting credit from a person's combined credits (Tier 3 evidence)."""

    media_type: str  # "movie" | "tv"
    tmdb_id: int
    title: str
    year: int | None
    popularity: float
    character: str


def _is_self_appearance(character: str | None) -> bool:
    """Talk shows / awards / archive footage credit the person as 'Self'."""
    c = (character or "").lower()
    return not c or "self" in c or "himself" in c or "herself" in c or "archive" in c


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
                    popularity=item.get("popularity") or 0.0,
                    vote_count=item.get("vote_count") or 0,
                )
            )
        # Year is a soft ranking signal only — never a filter (see rank_matches).
        return rank_matches(query, year, matches)

    async def search_person(self, name: str) -> int | None:
        """Best-match person id for an actor name, or None."""
        resp = await self._client.get(
            f"{TMDB_BASE}/search/person",
            params={"api_key": self.api_key, "query": name, "include_adult": "false"},
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0]["id"] if results else None

    async def combined_credits(self, person_id: int) -> list[PersonCredit]:
        """Acting credits for a person, excluding self-appearances (talk shows)."""
        resp = await self._client.get(
            f"{TMDB_BASE}/person/{person_id}/combined_credits",
            params={"api_key": self.api_key},
        )
        resp.raise_for_status()
        credits: list[PersonCredit] = []
        for item in resp.json().get("cast", []):
            media_type = item.get("media_type")
            if media_type not in ("movie", "tv"):
                continue
            if _is_self_appearance(item.get("character")):
                continue
            date = item.get("release_date") or item.get("first_air_date") or ""
            year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
            credits.append(
                PersonCredit(
                    media_type=media_type,
                    tmdb_id=item["id"],
                    title=item.get("title") or item.get("name") or "",
                    year=year,
                    popularity=item.get("popularity") or 0.0,
                    character=item.get("character") or "",
                )
            )
        return credits

    async def top_cast_characters(
        self, media_type: str, tmdb_id: int, limit: int = 25
    ) -> list[str]:
        """Character names of the top-billed cast (Tier 3 verification)."""
        kind = "movie" if media_type == "movie" else "tv"
        resp = await self._client.get(
            f"{TMDB_BASE}/{kind}/{tmdb_id}/credits", params={"api_key": self.api_key}
        )
        resp.raise_for_status()
        cast = resp.json().get("cast", [])[:limit]
        return [c.get("character") or "" for c in cast]

    async def resolve_tvdb_id(self, tmdb_id: int) -> int | None:
        """TMDB external IDs endpoint — Sonarr requires tvdbId."""
        resp = await self._client.get(
            f"{TMDB_BASE}/tv/{tmdb_id}/external_ids", params={"api_key": self.api_key}
        )
        resp.raise_for_status()
        return resp.json().get("tvdb_id")

    async def aclose(self) -> None:
        await self._client.aclose()
