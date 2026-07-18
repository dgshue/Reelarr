"""Inbound webhooks.

WhatsApp (Evolution API) posts `messages.upsert` events here — the only
channel that is webhook-driven rather than socket/polling-driven.

Webhook calls authenticate with Reelarr's API key regardless of UI auth mode
(spec §1). TODO(auth): enforce once General settings/API-key middleware lands.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, event: dict = Body(...)) -> dict:
    channel = getattr(request.app.state, "whatsapp_channel", None)
    if channel is None:
        # WhatsApp Source not enabled — accept and drop so Evolution doesn't retry-spam.
        return {"ok": True, "handled": False}
    await channel.handle_webhook_event(event)
    return {"ok": True, "handled": True}
