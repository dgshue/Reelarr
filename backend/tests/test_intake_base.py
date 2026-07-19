"""IntakeChannel URL extraction + allowlist behavior + multi-select rendering."""

import pytest

from reelarr.intake.base import MultiSelectOption, extract_supported_url
from reelarr.intake.telegram import TelegramChannel, build_multi_select_rows
from reelarr.intake.whatsapp import WhatsAppChannel
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


# --- Telegram multi-select keyboard (spec §5.4) ---------------------------------


def _options(*selected: bool) -> list[MultiSelectOption]:
    titles = ["Inception (2010) — movie", "Memento (2000) — movie", "Primer (2004) — movie"]
    return [
        MultiSelectOption(
            request_id=7, index=i + 1, label=titles[i], tmdb_id=100 + i,
            media_type="movie", selected=sel,
        )
        for i, sel in enumerate(selected)
    ]


def test_multi_select_keyboard_toggle_state_and_callbacks():
    rows = build_multi_select_rows(_options(False, True, False))
    labels = [row[0][0] for row in rows[:3]]
    assert labels == [
        "☐ Inception (2010) — movie",
        "☑ Memento (2000) — movie",
        "☐ Primer (2004) — movie",
    ]
    # Toggle callbacks carry the request id + 1-based index
    assert [row[0][1] for row in rows[:3]] == ["msel:7:t:1", "msel:7:t:2", "msel:7:t:3"]
    # Add-selected reflects the live count; Add all / None of these present
    assert rows[3] == [("➕ Add selected (1)", "msel:7:c")]
    assert rows[4] == [("✅ Add all", "msel:7:a"), ("❌ None of these", "msel:7:n")]


def test_multi_select_keyboard_confirm_all_reprompt():
    rows = build_multi_select_rows(_options(True, True, True), confirm_all=True)
    assert rows == [
        [("✅ Yes — add all 3", "msel:7:y")],
        [("↩️ Back", "msel:7:b")],
    ]


# --- WhatsApp numbered-reply multi-select fallback (spec §5.4) -------------------


def make_whatsapp_with_pending():
    channel = WhatsAppChannel("http://evolution:8080", "key")
    channel._pending_multi["555"] = (9, 3, False)
    sent: list[str] = []
    events: list[tuple] = []

    async def fake_send(chat_ref, text):
        sent.append(text)

    async def handler(request_id, action, indexes, chat_ref, message_ref):
        events.append((request_id, action, indexes))

    channel.send_text = fake_send
    channel.on_multi_select(handler)
    return channel, sent, events


@pytest.mark.asyncio
async def test_whatsapp_numbered_reply_selects_and_confirms():
    channel, _sent, events = make_whatsapp_with_pending()
    assert await channel._handle_multi_reply("555", "1, 3")
    assert events == [(9, "replace", [1, 3]), (9, "confirm", None)]
    assert "555" not in channel._pending_multi


@pytest.mark.asyncio
async def test_whatsapp_all_reprompts_then_yes_confirms():
    channel, sent, events = make_whatsapp_with_pending()
    assert await channel._handle_multi_reply("555", "all")
    assert events == []  # nothing fired yet — Add all re-prompts (spec §5.4)
    assert "reply 'yes'" in sent[-1]
    assert channel._pending_multi["555"] == (9, 3, True)
    assert await channel._handle_multi_reply("555", "yes")
    assert events == [(9, "confirm_all", None)]


@pytest.mark.asyncio
async def test_whatsapp_zero_dismisses_and_bad_input_reprompts():
    channel, sent, events = make_whatsapp_with_pending()
    assert await channel._handle_multi_reply("555", "9")  # out of range
    assert events == []
    assert "between 1 and 3" in sent[-1]
    assert await channel._handle_multi_reply("555", "0")
    assert events == [(9, "none", None)]
    # A link (non-selection text) is not consumed by the multi prompt
    channel._pending_multi["555"] = (9, 3, False)
    assert not await channel._handle_multi_reply("555", "https://www.tiktok.com/t/x/")


def test_multi_select_callback_data_stays_within_telegram_limit():
    # Telegram rejects callback_data over 64 bytes — even huge ids must fit
    options = [
        MultiSelectOption(
            request_id=2**31, index=10, label="X" * 200, tmdb_id=1, media_type="movie"
        )
    ]
    for row in build_multi_select_rows(options):
        for _label, data in row:
            assert len(data.encode()) <= 64
