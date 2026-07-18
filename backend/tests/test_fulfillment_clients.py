"""Radarr / Sonarr / Seerr clients against mocked HTTP endpoints (respx)."""

import httpx
import pytest
import respx

from reelarr.fulfillment.arr import DirectFulfillment, RadarrClient, SonarrClient
from reelarr.fulfillment.base import FulfillmentError, FulfillmentStatus
from reelarr.fulfillment.seerr import SeerrClient
from reelarr.pipeline.tmdb import TmdbMatch

pytestmark = pytest.mark.asyncio

RADARR = "http://radarr:7878"
SONARR = "http://sonarr:8989"
SEERR = "http://overseerr:5055"

HEAT = TmdbMatch(tmdb_id=949, title="Heat", year=1995, media_type="movie")
SEVERANCE = TmdbMatch(tmdb_id=95396, title="Severance", year=2022, media_type="tv", tvdb_id=371980)


# --- Radarr -------------------------------------------------------------------


@respx.mock
async def test_radarr_test_returns_live_populate_payload():
    """Seerr-style Test: one call validates AND returns dropdown data (spec §2)."""
    respx.get(f"{RADARR}/api/v3/system/status").respond(json={"version": "5.14.0"})
    respx.get(f"{RADARR}/api/v3/rootfolder").respond(json=[{"id": 1, "path": "/data/movies"}])
    respx.get(f"{RADARR}/api/v3/qualityprofile").respond(json=[{"id": 4, "name": "HD-1080p"}])
    respx.get(f"{RADARR}/api/v3/tag").respond(json=[{"id": 1, "label": "reelarr"}])

    result = await RadarrClient(RADARR, "key").test()
    assert result["ok"] is True
    assert result["version"] == "5.14.0"
    assert result["rootFolders"] == [{"id": 1, "path": "/data/movies"}]
    assert result["qualityProfiles"] == [{"id": 4, "name": "HD-1080p"}]
    assert result["tags"] == [{"id": 1, "label": "reelarr"}]


@respx.mock
async def test_radarr_test_sends_api_key_header():
    route = respx.get(f"{RADARR}/api/v3/system/status").respond(json={"version": "5"})
    respx.get(f"{RADARR}/api/v3/rootfolder").respond(json=[])
    respx.get(f"{RADARR}/api/v3/qualityprofile").respond(json=[])
    respx.get(f"{RADARR}/api/v3/tag").respond(json=[])
    await RadarrClient(RADARR, "secret-key").test()
    assert route.calls[0].request.headers["X-Api-Key"] == "secret-key"


@respx.mock
async def test_radarr_add_movie_payload_and_success():
    route = respx.post(f"{RADARR}/api/v3/movie").respond(201, json={"id": 10})
    client = RadarrClient(RADARR, "key", root_folder="/data/movies", quality_profile_id=4)
    result = await client.add_movie(HEAT.tmdb_id, HEAT.title, HEAT.year)
    assert result.status == FulfillmentStatus.ADDED

    import json
    sent = json.loads(route.calls[0].request.content)
    assert sent["tmdbId"] == 949
    assert sent["qualityProfileId"] == 4
    assert sent["rootFolderPath"] == "/data/movies"
    assert sent["monitored"] is True
    assert sent["addOptions"] == {"searchForMovie": True}


@respx.mock
async def test_radarr_already_exists_handled_gracefully():
    respx.post(f"{RADARR}/api/v3/movie").respond(
        400,
        json=[{"errorCode": "MovieExistsValidator", "errorMessage": "This movie has already been added"}],
    )
    result = await RadarrClient(RADARR, "key").add_movie(949, "Heat", 1995)
    assert result.status == FulfillmentStatus.ALREADY_EXISTS


@respx.mock
async def test_radarr_other_400_raises():
    respx.post(f"{RADARR}/api/v3/movie").respond(400, json=[{"errorMessage": "Invalid root folder"}])
    with pytest.raises(FulfillmentError):
        await RadarrClient(RADARR, "key").add_movie(949, "Heat", 1995)


# --- Sonarr --------------------------------------------------------------------


@respx.mock
async def test_sonarr_add_series_payload():
    route = respx.post(f"{SONARR}/api/v3/series").respond(201, json={"id": 20})
    client = SonarrClient(SONARR, "key", root_folder="/data/tv", quality_profile_id=6)
    result = await client.add_series(SEVERANCE.tvdb_id, SEVERANCE.title)
    assert result.status == FulfillmentStatus.ADDED

    import json
    sent = json.loads(route.calls[0].request.content)
    assert sent["tvdbId"] == 371980
    assert sent["rootFolderPath"] == "/data/tv"
    assert sent["addOptions"] == {"searchForMissingEpisodes": True}


# --- DirectFulfillment routing ----------------------------------------------------


@respx.mock
async def test_direct_fulfillment_routes_movie_to_radarr_tv_to_sonarr():
    radarr_route = respx.post(f"{RADARR}/api/v3/movie").respond(201, json={})
    sonarr_route = respx.post(f"{SONARR}/api/v3/series").respond(201, json={})
    direct = DirectFulfillment(RadarrClient(RADARR, "k"), SonarrClient(SONARR, "k"))

    await direct.fulfill(HEAT)
    assert radarr_route.called and not sonarr_route.called

    await direct.fulfill(SEVERANCE)
    assert sonarr_route.called


async def test_direct_fulfillment_tv_without_tvdbid_errors():
    no_tvdb = TmdbMatch(tmdb_id=1, title="Mystery Show", year=2020, media_type="tv", tvdb_id=None)
    direct = DirectFulfillment(RadarrClient(RADARR, "k"), SonarrClient(SONARR, "k"))
    with pytest.raises(FulfillmentError, match="tvdbId"):
        await direct.fulfill(no_tvdb)


# --- Seerr ------------------------------------------------------------------------


@respx.mock
async def test_seerr_test_validates_connectivity_and_auth():
    respx.get(f"{SEERR}/api/v1/status").respond(json={"version": "2.7.3"})
    respx.get(f"{SEERR}/api/v1/auth/me").respond(json={"id": 1})
    result = await SeerrClient(SEERR, "key").test()
    assert result == {"ok": True, "version": "2.7.3"}


@respx.mock
async def test_seerr_movie_request_payload():
    route = respx.post(f"{SEERR}/api/v1/request").respond(201, json={"id": 1})
    result = await SeerrClient(SEERR, "key").fulfill(HEAT)
    assert result.status == FulfillmentStatus.ADDED

    import json
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"mediaType": "movie", "mediaId": 949}


@respx.mock
async def test_seerr_tv_request_includes_seasons():
    route = respx.post(f"{SEERR}/api/v1/request").respond(201, json={"id": 2})
    await SeerrClient(SEERR, "key").fulfill(SEVERANCE)
    import json
    sent = json.loads(route.calls[0].request.content)
    assert sent["mediaType"] == "tv"
    assert sent["seasons"] == "all"


@respx.mock
async def test_seerr_already_requested_handled_like_already_exists():
    respx.post(f"{SEERR}/api/v1/request").respond(409, json={"message": "Request already exists"})
    result = await SeerrClient(SEERR, "key").fulfill(HEAT)
    assert result.status == FulfillmentStatus.ALREADY_EXISTS


@respx.mock
async def test_seerr_failure_raises():
    respx.post(f"{SEERR}/api/v1/request").respond(500, json={"message": "boom"})
    with pytest.raises(FulfillmentError):
        await SeerrClient(SEERR, "key").fulfill(HEAT)
