"""Discord intake channel — structural stub.

Shape is real (same IntakeChannel contract as Telegram); the discord.py
wiring is TODO until a bot token exists. Install extra: `pip install reelarr[discord]`.

Planned implementation notes (spec §4):
- discord.py client with message_content intent
- confirmation via native message components (buttons, <=5/row, <=25 total)
- Test fetches the bot's guild list so setup can offer a channel dropdown
  per guild instead of free-text channel IDs
"""

from __future__ import annotations

import logging

from reelarr.intake.base import ConfirmationCandidate, IntakeChannel

logger = logging.getLogger(__name__)


class DiscordChannel(IntakeChannel):
    channel_type = "discord"

    def __init__(self, bot_token: str, allowed_channel_ids: set[str] | None = None) -> None:
        super().__init__()
        self.bot_token = bot_token
        self.allowlist = allowed_channel_ids or set()
        self._client = None  # discord.Client

    async def start(self) -> None:
        # TODO(discord): needs a real bot token from the Developer Portal.
        # import discord
        # intents = discord.Intents.default(); intents.message_content = True
        # self._client = discord.Client(intents=intents)
        # ... on_message -> extract_supported_url -> self._link_handler
        # ... on_interaction (button) -> self._confirmation_handler
        raise NotImplementedError("Discord channel not wired yet — needs DISCORD_BOT_TOKEN")

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def test(self) -> dict:
        """Validates the bot token and returns the guild list (live-populates
        the guild -> channel dropdowns, spec §2)."""
        import httpx

        if not self.bot_token:
            return {"ok": False, "error": "No bot token configured"}
        headers = {"Authorization": f"Bot {self.bot_token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            me = await client.get("https://discord.com/api/v10/users/@me", headers=headers)
            if me.status_code != 200:
                return {"ok": False, "error": f"Token rejected ({me.status_code})"}
            guilds = await client.get(
                "https://discord.com/api/v10/users/@me/guilds", headers=headers
            )
        return {
            "ok": True,
            "bot_username": me.json().get("username"),
            "guilds": [
                {"id": g["id"], "name": g["name"]} for g in guilds.json()
            ] if guilds.status_code == 200 else [],
        }

    async def send_text(self, chat_ref: str, text: str) -> None:
        raise NotImplementedError("Discord channel not wired yet")

    async def send_confirmation(
        self, chat_ref: str, prompt: str, candidates: list[ConfirmationCandidate]
    ) -> None:
        raise NotImplementedError("Discord channel not wired yet")

    # send_multi_select (spec §5.4): when this adapter is wired, use a native
    # string select menu — discord.ui.Select(min_values=0,
    # max_values=len(options), options<=25; Reelarr's cap of 10 is well under
    # the platform limit) plus a confirm button. The menu interaction submits
    # the whole selection at once: forward it via the handler's "replace"
    # action, then "confirm" on the button — no per-toggle round-trips and no
    # update_multi_select needed (the base no-arg contract already fits).
