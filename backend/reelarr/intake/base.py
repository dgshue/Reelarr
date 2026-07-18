"""IntakeChannel abstraction (spec §4).

Each channel adapter:
- receives messages, extracts a supported social-video URL, checks the
  allowlist, and emits a normalized InboundLink to the registered handler
- can send plain text back to the originating chat
- can present a confirmation prompt (inline buttons where the platform
  supports them; numbered text replies where they're unreliable — WhatsApp)

Multiple channels run simultaneously; each is independently configured in
Settings -> Sources with its own allowlist.
"""

from __future__ import annotations

import abc
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

URL_PATTERN = re.compile(
    r"https?://(?:www\.)?"
    r"(?:instagram\.com|tiktok\.com|vm\.tiktok\.com|facebook\.com|fb\.watch)"
    r"/\S+",
    re.IGNORECASE,
)


def extract_supported_url(text: str) -> str | None:
    match = URL_PATTERN.search(text or "")
    return match.group(0) if match else None


@dataclass
class InboundLink:
    """Normalized 'someone shared a link' event."""

    url: str
    channel: str          # telegram / discord / slack / whatsapp
    chat_ref: str         # where to reply (chat/channel id, phone number)
    user_ref: str | None = None
    message_ref: str | None = None
    raw_text: str | None = None


@dataclass
class ConfirmationCandidate:
    """One selectable option in a confirmation prompt (top-3 TMDB matches)."""

    request_id: int
    index: int            # 1-based; also the numbered-reply digit on WhatsApp
    label: str            # e.g. "Heat (1995) — movie"
    tmdb_id: int
    media_type: str


# Handler types wired in by the intake service
LinkHandler = Callable[[InboundLink], Awaitable[None]]
# (request_id, selected index | None for "none of these", chat_ref)
ConfirmationHandler = Callable[[int, int | None, str], Awaitable[None]]


class IntakeChannel(abc.ABC):
    """Base class for all Source adapters."""

    channel_type: str = "base"

    def __init__(self) -> None:
        self._link_handler: LinkHandler | None = None
        self._confirmation_handler: ConfirmationHandler | None = None
        self.allowlist: set[str] = set()

    def on_link(self, handler: LinkHandler) -> None:
        self._link_handler = handler

    def on_confirmation(self, handler: ConfirmationHandler) -> None:
        self._confirmation_handler = handler

    def is_allowed(self, *refs: str | None) -> bool:
        """True if any provided ref (chat id / user id / number) is allowlisted.
        An empty allowlist denies everything — closed by default."""
        return any(r is not None and str(r) in self.allowlist for r in refs)

    # --- lifecycle ---------------------------------------------------------

    @abc.abstractmethod
    async def start(self) -> None:
        """Connect and begin receiving messages."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Disconnect cleanly."""

    @abc.abstractmethod
    async def test(self) -> dict:
        """Test-button contract (spec §2 table). Returns a payload with at
        least {"ok": bool}; channels that can enumerate (Discord guilds,
        Slack channels) include that data for live-populating dropdowns."""

    # --- outbound ----------------------------------------------------------

    @abc.abstractmethod
    async def send_text(self, chat_ref: str, text: str) -> None:
        """Plain text message to a chat."""

    @abc.abstractmethod
    async def send_confirmation(
        self, chat_ref: str, prompt: str, candidates: list[ConfirmationCandidate]
    ) -> None:
        """Channel-appropriate interactive prompt: inline buttons where
        reliable, numbered text replies otherwise. Always includes an implicit
        'None of these' option."""
