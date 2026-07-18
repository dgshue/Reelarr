"""WhatsApp intake channel via a self-hosted Evolution API instance — stub.

OPT-IN / ADVANCED (spec §4): unofficial Baileys engine, real ban risk.
Defaults off; ships behind the `whatsapp` compose profile. Confirmation uses
NUMBERED TEXT REPLIES ("Reply 1, 2, or 3 — or 0 for none"), NOT native
buttons, which are unreliable on the unofficial engine.

Evolution API integration shape:
- inbound: Evolution posts `messages.upsert` webhook events to
  POST /api/v1/webhooks/whatsapp (see reelarr.api.webhooks)
- outbound: POST {base}/message/sendText/{instance}
- session: QR pairing, so Test reports session state (open/connecting/closed)
  rather than validating a stateless token; needs a re-pair path the other
  channels don't.
"""

from __future__ import annotations

import logging

import httpx

from reelarr.intake.base import ConfirmationCandidate, InboundLink, IntakeChannel, extract_supported_url

logger = logging.getLogger(__name__)


class WhatsAppChannel(IntakeChannel):
    channel_type = "whatsapp"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        instance: str = "reelarr",
        allowed_numbers: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.api_key = api_key
        self.instance = instance
        self.allowlist = allowed_numbers or set()
        self._client: httpx.AsyncClient | None = None
        # Pending numbered-reply confirmations: chat_ref -> request_id
        self._pending: dict[str, int] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {"apikey": self.api_key}

    async def start(self) -> None:
        # Webhook-driven — nothing to poll. The webhook router calls
        # handle_webhook_event(); start() just prepares the HTTP client.
        # TODO(whatsapp): register/verify the webhook config on the Evolution
        # instance ( POST {base}/webhook/set/{instance} ) once credentials exist.
        if not self.base_url:
            raise NotImplementedError("WhatsApp channel not configured — needs EVOLUTION_API_URL")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def test(self) -> dict:
        """Instance reachable + session paired (post-QR-scan). Session status
        only — nothing to live-populate (spec §2)."""
        if not self.base_url:
            return {"ok": False, "error": "No Evolution API URL configured"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.base_url}/instance/connectionState/{self.instance}",
                headers=self._headers,
            )
            if resp.status_code != 200:
                return {"ok": False, "error": f"Evolution API returned {resp.status_code}"}
            state = resp.json().get("instance", {}).get("state", "unknown")
        return {"ok": state == "open", "session_state": state}

    # --- webhook entrypoint (called by reelarr.api.webhooks) ----------------

    async def handle_webhook_event(self, event: dict) -> None:
        """Process an Evolution `messages.upsert` payload.

        TODO(whatsapp): verify exact payload shape against a live Evolution
        instance — field names below follow Evolution API v2 docs.
        """
        if event.get("event") != "messages.upsert":
            return
        data = event.get("data", {})
        key = data.get("key", {})
        if key.get("fromMe"):
            return
        remote_jid = key.get("remoteJid", "")  # e.g. "15551234567@s.whatsapp.net"
        number = remote_jid.split("@")[0]
        if not self.is_allowed(number):
            logger.info("whatsapp: ignoring message from non-allowlisted number %s", number)
            return
        text = (
            data.get("message", {}).get("conversation")
            or data.get("message", {}).get("extendedTextMessage", {}).get("text")
            or ""
        )

        # Numbered-reply confirmation path
        if number in self._pending and text.strip().isdigit():
            request_id = self._pending.pop(number)
            selection = int(text.strip())
            if self._confirmation_handler is not None:
                await self._confirmation_handler(
                    request_id, selection if selection > 0 else None, number
                )
            return

        url = extract_supported_url(text)
        if url and self._link_handler is not None:
            await self._link_handler(
                InboundLink(
                    url=url,
                    channel=self.channel_type,
                    chat_ref=number,
                    user_ref=number,
                    message_ref=key.get("id"),
                    raw_text=text,
                )
            )

    # --- outbound ------------------------------------------------------------

    async def send_text(self, chat_ref: str, text: str) -> None:
        assert self._client is not None, "channel not started"
        resp = await self._client.post(
            f"{self.base_url}/message/sendText/{self.instance}",
            headers=self._headers,
            json={"number": chat_ref, "text": text},
        )
        resp.raise_for_status()

    async def send_confirmation(
        self, chat_ref: str, prompt: str, candidates: list[ConfirmationCandidate]
    ) -> None:
        """Numbered text replies — native buttons are unreliable on Baileys."""
        lines = [prompt, ""]
        for c in candidates:
            lines.append(f"{c.index}. {c.label}")
        lines.append("0. None of these")
        lines.append("")
        lines.append("Reply with a number.")
        if candidates:
            self._pending[chat_ref] = candidates[0].request_id
        await self.send_text(chat_ref, "\n".join(lines))
