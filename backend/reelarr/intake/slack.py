"""Slack intake channel — structural stub.

Uses Socket Mode (slack-bolt) so no public HTTPS endpoint is required (right
call for a homelab, spec §4). Install extra: `pip install reelarr[slack]`.

Planned implementation notes:
- slack_bolt.async_app.AsyncApp + AsyncSocketModeHandler (needs both a bot
  token `xoxb-...` and an app-level token `xapp-...` with connections:write)
- confirmation via Block Kit buttons (action_id "confirm", value
  "<request_id>:<index>")
- Test does auth.test and fetches the channel list for the allowlist dropdown
"""

from __future__ import annotations

import logging

from reelarr.intake.base import ConfirmationCandidate, IntakeChannel

logger = logging.getLogger(__name__)


class SlackChannel(IntakeChannel):
    channel_type = "slack"

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        allowed_channel_ids: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.bot_token = bot_token
        self.app_token = app_token  # Socket Mode app-level token
        self.allowlist = allowed_channel_ids or set()
        self._handler = None  # AsyncSocketModeHandler

    async def start(self) -> None:
        # TODO(slack): needs real SLACK_BOT_TOKEN + SLACK_APP_TOKEN.
        # from slack_bolt.async_app import AsyncApp
        # from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        # app = AsyncApp(token=self.bot_token)
        # @app.event("message") -> extract_supported_url -> self._link_handler
        # @app.action("confirm") -> self._confirmation_handler
        # self._handler = AsyncSocketModeHandler(app, self.app_token)
        # await self._handler.connect_async()
        raise NotImplementedError("Slack channel not wired yet — needs SLACK_BOT_TOKEN/SLACK_APP_TOKEN")

    async def stop(self) -> None:
        if self._handler is not None:
            await self._handler.close_async()
            self._handler = None

    async def test(self) -> dict:
        """auth.test + channel list (live-populates the allowlist dropdown)."""
        import httpx

        if not self.bot_token:
            return {"ok": False, "error": "No bot token configured"}
        headers = {"Authorization": f"Bearer {self.bot_token}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            auth = (await client.post("https://slack.com/api/auth.test", headers=headers)).json()
            if not auth.get("ok"):
                return {"ok": False, "error": auth.get("error", "auth.test failed")}
            chans = (
                await client.get(
                    "https://slack.com/api/conversations.list",
                    headers=headers,
                    params={"types": "public_channel,private_channel", "limit": 200},
                )
            ).json()
        return {
            "ok": True,
            "team": auth.get("team"),
            "bot_user": auth.get("user"),
            "channels": [
                {"id": c["id"], "name": c["name"]} for c in chans.get("channels", [])
            ] if chans.get("ok") else [],
        }

    async def send_text(self, chat_ref: str, text: str) -> None:
        raise NotImplementedError("Slack channel not wired yet")

    async def send_confirmation(
        self, chat_ref: str, prompt: str, candidates: list[ConfirmationCandidate]
    ) -> None:
        raise NotImplementedError("Slack channel not wired yet")
