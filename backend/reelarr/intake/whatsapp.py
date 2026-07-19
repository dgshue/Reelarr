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
import re

import httpx

from reelarr.intake.base import (
    ConfirmationCandidate,
    InboundLink,
    IntakeChannel,
    MultiSelectOption,
    extract_supported_url,
)

logger = logging.getLogger(__name__)

# Reply like "1,3,5" / "1 3 5" — indexes to add (multi-select fallback)
_NUMBERED_REPLY = re.compile(r"^\s*\d+(?:[\s,]+\d+)*\s*$")


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
        # Pending multi-select prompts: chat_ref -> (request_id, option count,
        # awaiting "yes" for add-all). Only the *prompt routing* lives here —
        # selection state itself persists on the request row (spec §5.4).
        self._pending_multi: dict[str, tuple[int, int, bool]] = {}

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

        # Numbered-reply multi-select path ("1,3,5" / "all" / "0")
        if number in self._pending_multi and self._multi_select_handler is not None:
            handled = await self._handle_multi_reply(number, text.strip())
            if handled:
                return

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

    async def _handle_multi_reply(self, number: str, text: str) -> bool:
        """Interpret a reply to a pending multi-select prompt. Returns True if
        the message was consumed as a multi-select interaction."""
        assert self._multi_select_handler is not None
        request_id, count, awaiting_yes = self._pending_multi[number]
        lowered = text.lower()

        if awaiting_yes:
            self._pending_multi.pop(number)
            if lowered in ("yes", "y"):
                await self._multi_select_handler(request_id, "confirm_all", None, number, None)
            else:
                await self.send_text(number, "Cancelled — nothing was added.")
            return True

        if lowered == "0":
            self._pending_multi.pop(number)
            await self._multi_select_handler(request_id, "none", None, number, None)
            return True
        if lowered == "all":
            # "Add all" re-prompts before firing (spec §5.4)
            self._pending_multi[number] = (request_id, count, True)
            await self.send_text(
                number, f"That will add all {count} titles — reply 'yes' to confirm, or 0 to cancel."
            )
            return True
        if _NUMBERED_REPLY.match(text):
            indexes = sorted({int(tok) for tok in re.split(r"[\s,]+", text) if tok})
            if all(1 <= i <= count for i in indexes):
                self._pending_multi.pop(number)
                await self._multi_select_handler(request_id, "replace", indexes, number, None)
                await self._multi_select_handler(request_id, "confirm", None, number, None)
            else:
                await self.send_text(number, f"Pick numbers between 1 and {count}, 'all', or 0.")
            return True
        return False  # not a selection reply — fall through (may be a new link)

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

    async def send_multi_select(
        self, chat_ref: str, prompt: str, options: list[MultiSelectOption]
    ) -> str | None:
        """Numbered-reply fallback (spec §5.4) — reply "1,3,5", "all", or 0."""
        lines = [prompt, ""]
        for o in options:
            lines.append(f"{o.index}. {o.label}")
        lines.append("")
        lines.append("Reply with the numbers to add (e.g. 1,3,5), 'all' for all, or 0 for none.")
        if options:
            self._pending_multi[chat_ref] = (options[0].request_id, len(options), False)
        await self.send_text(chat_ref, "\n".join(lines))
        return None  # Evolution send API: no message ref we can edit anyway

    async def update_multi_select(
        self, chat_ref: str, message_ref: str | None, options: list[MultiSelectOption],
        confirm_all: bool = False,
    ) -> None:
        """No in-place editing on WhatsApp — selection arrives whole in one
        numbered reply, so there is nothing to re-render."""
