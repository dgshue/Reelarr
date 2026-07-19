"""TikTok comment fallback.

yt-dlp has no TikTok comment extractor — it reports `comment_count` in the
thousands while returning none — so the strongest signal for hashtag-spam
captions was silently discarded. These cover the fallback that fetches them
from TikTok's own web API, and that it degrades quietly when unavailable.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from reelarr.pipeline.media import YtDlpResolver

COMMENT_URL = "https://www.tiktok.com/api/comment/list/"


def _resolver(tmp_path: Path) -> YtDlpResolver:
    return YtDlpResolver(tmp_dir=tmp_path, cookies_dir=tmp_path / "cookies")


@respx.mock
async def test_returns_comments_sorted_by_likes(tmp_path: Path) -> None:
    respx.get(COMMENT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "comments": [
                    {"text": "mid take", "digg_count": 3},
                    {"text": "Excellent movie the gorge", "digg_count": 13972},
                    {"text": "the gorge best movie", "digg_count": 1991},
                ]
            },
        )
    )
    got = await _resolver(tmp_path)._fetch_tiktok_comments("7471637564726922542")
    assert got == [
        "Excellent movie the gorge",
        "the gorge best movie",
        "mid take",
    ]


@respx.mock
async def test_respects_limit(tmp_path: Path) -> None:
    respx.get(COMMENT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"comments": [{"text": f"c{i}", "digg_count": i} for i in range(40)]},
        )
    )
    got = await _resolver(tmp_path)._fetch_tiktok_comments("1", limit=5)
    assert len(got) == 5
    assert got[0] == "c39"  # highest-liked first


@respx.mock
async def test_sends_the_aid_param_that_makes_the_endpoint_work(tmp_path: Path) -> None:
    """Without aid=1988 TikTok returns status_code 5 and an empty body."""
    route = respx.get(COMMENT_URL).mock(
        return_value=httpx.Response(200, json={"comments": []})
    )
    await _resolver(tmp_path)._fetch_tiktok_comments("123")
    assert route.calls.last.request.url.params["aid"] == "1988"
    assert route.calls.last.request.url.params["aweme_id"] == "123"


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"status_code": 5}),  # the no-aid failure shape
    ],
)
async def test_failures_are_non_fatal(tmp_path: Path, response: httpx.Response) -> None:
    """Comments are an optional signal — never fail the request over them."""
    respx.get(COMMENT_URL).mock(return_value=response)
    assert await _resolver(tmp_path)._fetch_tiktok_comments("123") == []


@respx.mock
async def test_network_error_is_non_fatal(tmp_path: Path) -> None:
    respx.get(COMMENT_URL).mock(side_effect=httpx.ConnectError("boom"))
    assert await _resolver(tmp_path)._fetch_tiktok_comments("123") == []


@respx.mock
async def test_blank_comments_are_dropped(tmp_path: Path) -> None:
    respx.get(COMMENT_URL).mock(
        return_value=httpx.Response(
            200,
            json={"comments": [{"text": "  ", "digg_count": 9}, {"text": "real", "digg_count": 1}]},
        )
    )
    assert await _resolver(tmp_path)._fetch_tiktok_comments("1") == ["real"]
