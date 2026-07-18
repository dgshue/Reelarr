"""TMDB client against mocked HTTP (respx)."""

import pytest
import respx

from reelarr.pipeline.tmdb import TMDB_BASE, TmdbClient

pytestmark = pytest.mark.asyncio


@respx.mock
async def test_search_multi_parses_and_filters_people():
    respx.get(f"{TMDB_BASE}/search/multi").respond(
        json={
            "results": [
                {"id": 1, "media_type": "person", "name": "Al Pacino"},
                {
                    "id": 949,
                    "media_type": "movie",
                    "title": "Heat",
                    "release_date": "1995-12-15",
                    "poster_path": "/heat.jpg",
                    "overview": "A crew of thieves...",
                },
                {
                    "id": 95396,
                    "media_type": "tv",
                    "name": "Severance",
                    "first_air_date": "2022-02-18",
                },
            ]
        }
    )
    matches = await TmdbClient("key").search_multi("heat")
    assert len(matches) == 2  # person filtered out
    assert matches[0].title == "Heat"
    assert matches[0].year == 1995
    assert matches[0].media_type == "movie"
    assert matches[0].poster_url.endswith("/heat.jpg")
    assert matches[1].title == "Severance"
    assert matches[1].media_type == "tv"


@respx.mock
async def test_search_multi_year_sort_prefers_identified_year():
    respx.get(f"{TMDB_BASE}/search/multi").respond(
        json={
            "results": [
                {"id": 2, "media_type": "movie", "title": "Heat", "release_date": "2013-01-01"},
                {"id": 949, "media_type": "movie", "title": "Heat", "release_date": "1995-12-15"},
            ]
        }
    )
    matches = await TmdbClient("key").search_multi("heat", year=1995)
    assert matches[0].tmdb_id == 949


@respx.mock
async def test_resolve_tvdb_id():
    respx.get(f"{TMDB_BASE}/tv/95396/external_ids").respond(json={"tvdb_id": 371980})
    assert await TmdbClient("key").resolve_tvdb_id(95396) == 371980
