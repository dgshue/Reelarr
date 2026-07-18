"""IntakeChannel URL extraction + allowlist behavior."""

from reelarr.intake.base import extract_supported_url
from reelarr.intake.telegram import TelegramChannel
from reelarr.pipeline.media import detect_platform


def test_extracts_supported_urls():
    cases = {
        "check this out https://www.instagram.com/reel/Cxyz123/": "instagram",
        "https://vm.tiktok.com/ZM123abc/": "tiktok",
        "https://www.tiktok.com/@user/video/728341": "tiktok",
        "lol https://fb.watch/abc123/ so good": "facebook",
        "https://www.facebook.com/watch?v=12345": "facebook",
    }
    for text, platform in cases.items():
        url = extract_supported_url(text)
        assert url is not None, text
        assert detect_platform(url) == platform


def test_ignores_unsupported_urls():
    assert extract_supported_url("https://www.youtube.com/watch?v=abc") is None
    assert extract_supported_url("no links here") is None
    assert extract_supported_url("") is None


def test_allowlist_closed_by_default():
    channel = TelegramChannel("token", allowed_chat_ids=set())
    assert not channel.is_allowed("12345")


def test_allowlist_matches_any_ref():
    channel = TelegramChannel("token", allowed_chat_ids={"111", "222"})
    assert channel.is_allowed("111")
    assert channel.is_allowed("999", "222")  # user id match even if chat id doesn't
    assert not channel.is_allowed("999", None)
