"""TMDB client against mocked HTTP (respx)."""

import pytest
import respx

from reelarr.pipeline.tmdb import TMDB_BASE, TmdbClient, TmdbMatch, rank_matches

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
async def test_search_multi_exact_title_beats_year_matched_junk():
    """The measured 'The Gorge' failure: the LLM hallucinated year=2023, and
    the old exact-year float ranked 'The Corpse in the Gorge (2023)' above the
    actual film. Exact title must dominate; the wrong year only demotes."""
    respx.get(f"{TMDB_BASE}/search/multi").respond(
        json={
            "results": [
                {"id": 950396, "media_type": "movie", "title": "The Gorge",
                 "release_date": "2025-02-13", "popularity": 22.99, "vote_count": 3932},
                {"id": 1154958, "media_type": "movie", "title": "The Corpse in the Gorge",
                 "release_date": "2023-06-01", "popularity": 0.15, "vote_count": 3},
                {"id": 964734, "media_type": "movie", "title": "The Gorge",
                 "release_date": "1968-01-01", "popularity": 0.24, "vote_count": 1},
            ]
        }
    )
    matches = await TmdbClient("key").search_multi("The Gorge", year=2023)
    assert [m.tmdb_id for m in matches] == [950396, 964734, 1154958]
    assert matches[0].popularity == 22.99  # parsed for downstream ranking/gating
    assert matches[0].vote_count == 3932


def test_rank_matches_year_is_soft_signal():
    def m(tmdb_id, title, year, pop=0.0):
        return TmdbMatch(tmdb_id=tmdb_id, title=title, year=year,
                         media_type="movie", popularity=pop)

    # Exact title with a distant year still beats a partial title at the
    # claimed year — a large gap is a demotion, never disqualifying.
    ranked = rank_matches("Heat", 1995, [m(1, "Heat Wave", 1995, 5.0), m(2, "Heat", 1972, 0.1)])
    assert [x.tmdb_id for x in ranked] == [2, 1]

    # Among exact-title ties, year proximity decides (±1-2 is near-free)...
    ranked = rank_matches("Heat", 1995, [m(1, "Heat", 2013, 9.0), m(2, "Heat", 1996, 0.1)])
    assert [x.tmdb_id for x in ranked] == [2, 1]

    # ...and with no year at all, popularity breaks the tie (the 'Digger'
    # case: three films named Digger, identified year unstable/None).
    ranked = rank_matches("Digger", None,
                          [m(1, "Digger", 1993, 0.6), m(2, "Digger", 2026, 20.8), m(3, "Digger", 2021, 0.7)])
    assert [x.tmdb_id for x in ranked] == [2, 3, 1]

    # Title normalization: case and whitespace never break an exact match.
    ranked = rank_matches("the  gorge", None, [m(1, "Gorge Deep", 2020, 9.0), m(2, "The Gorge", 2025, 1.0)])
    assert ranked[0].tmdb_id == 2


@respx.mock
async def test_resolve_tvdb_id():
    respx.get(f"{TMDB_BASE}/tv/95396/external_ids").respond(json={"tvdb_id": 371980})
    assert await TmdbClient("key").resolve_tvdb_id(95396) == 371980
