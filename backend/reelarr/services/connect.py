"""Connect — outbound notification dispatch (Settings -> Connect, spec §6).

v1 targets: Discord webhook, generic Webhook, Pushover, Slack webhook, ntfy,
Gotify, plus an Apprise passthrough covering 100+ services.

The dispatch plumbing and per-target `test()` (send a real test notification,
per the spec §2 Test-button table) are wired; message formatting is minimal
until the UI exposes per-event templates.
"""

from __future__ import annotations

import logging

import httpx

from reelarr.services.settings import ConnectTarget

logger = logging.getLogger(__name__)


async def send_notification(target: ConnectTarget, title: str, body: str) -> None:
    """Send one notification to one target. Raises on failure (callers decide
    whether that surfaces as a health issue)."""
    cfg = target.config
    async with httpx.AsyncClient(timeout=15.0) as client:
        if target.target_type == "discord":
            # config: {"webhook_url": ...}
            resp = await client.post(
                cfg["webhook_url"], json={"embeds": [{"title": title, "description": body}]}
            )
        elif target.target_type == "slack":
            # config: {"webhook_url": ...}
            resp = await client.post(cfg["webhook_url"], json={"text": f"*{title}*\n{body}"})
        elif target.target_type == "webhook":
            # config: {"url": ..., "method": "POST"}
            resp = await client.request(
                cfg.get("method", "POST"), cfg["url"], json={"title": title, "body": body}
            )
        elif target.target_type == "pushover":
            # config: {"user_key": ..., "api_token": ...}
            resp = await client.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": cfg["api_token"],
                    "user": cfg["user_key"],
                    "title": title,
                    "message": body,
                },
            )
        elif target.target_type == "ntfy":
            # config: {"server": "https://ntfy.sh", "topic": ...}
            resp = await client.post(
                f"{cfg.get('server', 'https://ntfy.sh').rstrip('/')}/{cfg['topic']}",
                content=body.encode(),
                headers={"Title": title},
            )
        elif target.target_type == "gotify":
            # config: {"server": ..., "app_token": ...}
            resp = await client.post(
                f"{cfg['server'].rstrip('/')}/message",
                params={"token": cfg["app_token"]},
                json={"title": title, "message": body, "priority": 5},
            )
        elif target.target_type == "apprise":
            # config: {"server": <apprise-api base>, "urls": "..."} — passthrough
            resp = await client.post(
                f"{cfg['server'].rstrip('/')}/notify",
                json={"urls": cfg["urls"], "title": title, "body": body},
            )
        elif target.target_type == "telegram":
            # config: {"bot_token": ..., "chat_id": ...} — independent of the
            # Telegram Source even though the platform overlaps (spec §6)
            resp = await client.post(
                f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
                json={"chat_id": cfg["chat_id"], "text": f"{title}\n{body}"},
            )
        else:
            raise ValueError(f"Unknown Connect target type: {target.target_type}")
    resp.raise_for_status()


async def test_target(target: ConnectTarget) -> dict:
    """Test button = send a real test notification (spec §2)."""
    try:
        await send_notification(target, "Reelarr", "Test notification from Reelarr 🎬")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
