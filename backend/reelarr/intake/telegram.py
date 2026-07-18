"""Telegram intake channel — the priority channel, fully implemented.

Uses python-telegram-bot (long polling; no public HTTPS endpoint needed).
Confirmation uses native inline-keyboard buttons with callback data
"confirm:<request_id>:<index>" ("0" = None of these).
"""

from __future__ import annotations

import logging

from reelarr.intake.base import ConfirmationCandidate, InboundLink, IntakeChannel, extract_supported_url

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Send me an Instagram Reel, TikTok, or Facebook video link and I'll figure "
    "out what movie or show it's from and add it to your library."
)


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

        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        self._app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^confirm:"))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info("telegram channel started (long polling)")

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
