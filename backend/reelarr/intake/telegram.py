"""Telegram intake channel — the priority channel, fully implemented.

Uses python-telegram-bot (long polling; no public HTTPS endpoint needed).
Confirmation uses native inline-keyboard buttons with callback data
"confirm:<request_id>:<index>" ("0" = None of these).

Multi-select (spec §5.4): Telegram has no native multi-select, so it's
simulated with toggle buttons that edit the message's keyboard in place
(tap "☐ Inception" -> "☑ Inception") plus "➕ Add selected (N)", "✅ Add all"
(which re-prompts before firing) and "❌ None of these". Callback data is
"msel:<request_id>:<action>[:<index>]". Selection state lives on the request
row in the DB, never here — a restart mid-selection loses nothing, because
every callback carries the chat/message refs needed to re-render.
"""

from __future__ import annotations

import logging

from reelarr.intake.base import (
    ConfirmationCandidate,
    InboundLink,
    IntakeChannel,
    MultiSelectOption,
    extract_supported_url,
)

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Send me an Instagram Reel, TikTok, or Facebook video link and I'll figure "
    "out what movie or show it's from and add it to your library."
)

# msel callback actions: single-letter on the wire (64-byte callback_data cap)
_MSEL_ACTIONS = {"t": "toggle", "c": "confirm", "a": "all", "y": "confirm_all", "b": "back", "n": "none"}


def build_multi_select_rows(
    options: list[MultiSelectOption], confirm_all: bool = False
) -> list[list[tuple[str, str]]]:
    """Keyboard layout as (label, callback_data) rows — pure, testable without
    the telegram package. confirm_all renders the Add-all re-prompt instead."""
    request_id = options[0].request_id if options else 0
    if confirm_all:
        return [
            [(f"✅ Yes — add all {len(options)}", f"msel:{request_id}:y")],
            [("↩️ Back", f"msel:{request_id}:b")],
        ]
    rows = [
        [(f"{'☑' if o.selected else '☐'} {o.label}", f"msel:{request_id}:t:{o.index}")]
        for o in options
    ]
    selected_count = sum(1 for o in options if o.selected)
    rows.append([(f"➕ Add selected ({selected_count})", f"msel:{request_id}:c")])
    rows.append(
        [("✅ Add all", f"msel:{request_id}:a"), ("❌ None of these", f"msel:{request_id}:n")]
    )
    return rows


class TelegramChannel(IntakeChannel):
    channel_type = "telegram"

    def __init__(self, bot_token: str, allowed_chat_ids: set[str] | None = None) -> None:
        super().__init__()
        self.bot_token = bot_token
        self.allowlist = allowed_chat_ids or set()
        self._app = None  # telegram.ext.Application

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        # Imported lazily so the package is only required when Telegram is enabled.
        from telegram import Update
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )

        self._app = Application.builder().token(self.bot_token).build()

        async def handle_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
            msg = update.effective_message
            chat = update.effective_chat
            if msg is None or chat is None:
                return
            if not self.is_allowed(chat.id, update.effective_user and update.effective_user.id):
                logger.info("telegram: ignoring message from non-allowlisted chat %s", chat.id)
                return
            url = extract_supported_url(msg.text or msg.caption or "")
            if url is None:
                await msg.reply_text(HELP_TEXT)
                return
            if self._link_handler is None:
                return
            await self._link_handler(
                InboundLink(
                    url=url,
                    channel=self.channel_type,
                    chat_ref=str(chat.id),
                    user_ref=str(update.effective_user.id) if update.effective_user else None,
                    message_ref=str(msg.message_id),
                    raw_text=msg.text or msg.caption,
                )
            )

        async def handle_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
            query = update.callback_query
            if query is None or query.data is None:
                return
            await query.answer()
            try:
                _tag, request_id, index = query.data.split(":")
            except ValueError:
                return
            if self._confirmation_handler is None:
                return
            chat_ref = str(query.message.chat.id) if query.message else ""
            selected = int(index) if index != "0" else None
            await self._confirmation_handler(int(request_id), selected, chat_ref)

        async def handle_msel_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
            query = update.callback_query
            if query is None or query.data is None:
                return
            await query.answer()
            parts = query.data.split(":")
            # msel:<request_id>:<action letter>[:<index>]
            if len(parts) < 3 or parts[2] not in _MSEL_ACTIONS:
                return
            try:
                request_id = int(parts[1])
                indexes = [int(parts[3])] if len(parts) > 3 else None
            except ValueError:
                return
            if self._multi_select_handler is None:
                return
            chat_ref = str(query.message.chat.id) if query.message else ""
            message_ref = str(query.message.message_id) if query.message else None
            await self._multi_select_handler(
                request_id, _MSEL_ACTIONS[parts[2]], indexes, chat_ref, message_ref
            )

        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        self._app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^confirm:"))
        self._app.add_handler(CallbackQueryHandler(handle_msel_callback, pattern=r"^msel:"))

        await self._app.initialize()
        await self._app.start()
        # Telegram allows exactly one getUpdates consumer per bot token. Any
        # competing call (a debug curl, a second instance, a redeploy overlap)
        # raises Conflict and — by default — kills the polling loop for good,
        # leaving the app "up" but deaf with no further log output. Recover
        # instead of dying silently.
        await self._app.updater.start_polling(error_callback=self._on_polling_error)
        logger.info("telegram channel started (long polling)")

    def _on_polling_error(self, exc: Exception) -> None:
        """Log polling errors loudly; PTB retries the loop rather than exiting."""
        from telegram.error import Conflict

        if isinstance(exc, Conflict):
            logger.error(
                "telegram polling conflict — another getUpdates consumer is using "
                "this bot token (a second Reelarr instance, or a manual API call). "
                "Retrying; if this repeats, make sure only one instance is running."
            )
        else:
            logger.error("telegram polling error: %s: %s", type(exc).__name__, exc)

    async def stop(self) -> None:
        if self._app is not None:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    # --- test button --------------------------------------------------------

    async def test(self) -> dict:
        """Validates the bot token via getMe. Nothing to live-populate — a bot
        that hasn't been messaged has no chat list to fetch (spec §2)."""
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{self.bot_token}/getMe")
            data = resp.json()
        if not data.get("ok"):
            return {"ok": False, "error": data.get("description", "invalid token")}
        me = data["result"]
        return {"ok": True, "bot_username": me.get("username"), "bot_name": me.get("first_name")}

    # --- outbound ------------------------------------------------------------

    async def send_text(self, chat_ref: str, text: str) -> None:
        assert self._app is not None, "channel not started"
        await self._app.bot.send_message(chat_id=int(chat_ref), text=text, parse_mode="Markdown")

    async def send_confirmation(
        self, chat_ref: str, prompt: str, candidates: list[ConfirmationCandidate]
    ) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        assert self._app is not None, "channel not started"
        rows = [
            [
                InlineKeyboardButton(
                    c.label, callback_data=f"confirm:{c.request_id}:{c.index}"
                )
            ]
            for c in candidates
        ]
        request_id = candidates[0].request_id if candidates else 0
        rows.append(
            [InlineKeyboardButton("❌ None of these", callback_data=f"confirm:{request_id}:0")]
        )
        await self._app.bot.send_message(
            chat_id=int(chat_ref), text=prompt, reply_markup=InlineKeyboardMarkup(rows)
        )

    async def send_multi_select(
        self, chat_ref: str, prompt: str, options: list[MultiSelectOption]
    ) -> str | None:
        assert self._app is not None, "channel not started"
        message = await self._app.bot.send_message(
            chat_id=int(chat_ref),
            text=prompt,
            reply_markup=self._msel_markup(build_multi_select_rows(options)),
        )
        return str(message.message_id)

    async def update_multi_select(
        self, chat_ref: str, message_ref: str | None, options: list[MultiSelectOption],
        confirm_all: bool = False,
    ) -> None:
        """Edit only the keyboard — the prompt text stays, so 'Back' from the
        Add-all re-prompt needs no stored copy of the original message."""
        from telegram.error import BadRequest

        assert self._app is not None, "channel not started"
        if message_ref is None:
            return
        try:
            await self._app.bot.edit_message_reply_markup(
                chat_id=int(chat_ref),
                message_id=int(message_ref),
                reply_markup=self._msel_markup(build_multi_select_rows(options, confirm_all)),
            )
        except BadRequest as exc:
            # "Message is not modified" — double-tap raced the edit; harmless.
            if "not modified" not in str(exc).lower():
                raise

    @staticmethod
    def _msel_markup(rows: list[list[tuple[str, str]]]):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
        )
