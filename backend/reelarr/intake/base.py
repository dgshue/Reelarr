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


@dataclass
class MultiSelectOption:
    """One toggleable title in a multi-select prompt (listicles, spec §5.4).

    `selected` mirrors the persisted per-candidate state on the request row —
    the DB is the source of truth (survives restarts); options are re-built
    from it on every render."""

    request_id: int
    index: int            # 1-based; also the numbered-reply digit on WhatsApp
    label: str            # e.g. "Inception (2010) — movie"
    tmdb_id: int
    media_type: str
    confidence: str = "high"   # per-title: high | medium | low
    selected: bool = False


# Handler types wired in by the intake service
LinkHandler = Callable[[InboundLink], Awaitable[None]]
# (request_id, selected index | None for "none of these", chat_ref)
ConfirmationHandler = Callable[[int, int | None, str], Awaitable[None]]
# (request_id, action, indexes | None, chat_ref, message_ref)
# Actions cover both interaction styles so one contract fits every platform:
# - "toggle":      indexes=[i] — tap-to-toggle platforms (Telegram)
# - "replace":     indexes = the full selection — native multi-select
#                  platforms (Discord string select) and numbered replies
#                  (WhatsApp "1,3,5") submit the whole set at once
# - "confirm":     add whatever is selected on the request row
# - "all":         user asked for Add-all -> processor re-prompts (spec §5.4)
# - "confirm_all": user confirmed the Add-all re-prompt
# - "back":        cancel the Add-all re-prompt, back to selection
# - "none":        none of these — dismiss + blocklist
MultiSelectHandler = Callable[[int, str, list[int] | None, str, str | None], Awaitable[None]]


class IntakeChannel(abc.ABC):
    """Base class for all Source adapters."""

    channel_type: str = "base"

    def __init__(self) -> None:
        self._link_handler: LinkHandler | None = None
        self._confirmation_handler: ConfirmationHandler | None = None
        self._multi_select_handler: MultiSelectHandler | None = None
        self.allowlist: set[str] = set()

    def on_link(self, handler: LinkHandler) -> None:
        self._link_handler = handler

    def on_confirmation(self, handler: ConfirmationHandler) -> None:
        self._confirmation_handler = handler

    def on_multi_select(self, handler: MultiSelectHandler) -> None:
        self._multi_select_handler = handler

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

    async def send_multi_select(
        self, chat_ref: str, prompt: str, options: list[MultiSelectOption]
    ) -> str | None:
        """Multi-select prompt for multi-title results (spec §5.4). Returns
        the sent message's ref where the platform exposes one (platforms that
        simulate multi-select by editing the message in place need it).

        Per-platform shape:
        - Telegram: toggle buttons edited in place + "Add selected (N)" /
          "Add all" / "None of these" (no native multi-select)
        - Discord: native string select menu (min_values=0,
          max_values=len(options), hard platform max 25 options) + confirm
          button — submits via the handler's "replace" action
        - WhatsApp: numbered text replies ("Reply 1,3,5 / all / 0")

        Selection state persists on the request row, not here — adapters are
        stateless renderers of the options they're given."""
        raise NotImplementedError(f"{self.channel_type} does not support multi-select yet")

    async def update_multi_select(
        self, chat_ref: str, message_ref: str | None, options: list[MultiSelectOption],
        confirm_all: bool = False,
    ) -> None:
        """Re-render an in-place multi-select after a toggle (platforms that
        simulate multi-select, e.g. Telegram). confirm_all=True renders the
        Add-all re-prompt ("Yes — add all N" / "Back") instead of the toggle
        rows. Platforms with native multi-select can no-op."""
        raise NotImplementedError(f"{self.channel_type} does not support multi-select yet")
