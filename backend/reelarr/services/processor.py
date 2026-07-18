"""Request processor — owns the request lifecycle around the pipeline.

Receives InboundLink events from intake channels, runs the identification
pipeline (spec §5 steps 1-5), and performs step 6 (fulfillment) or sends a
confirmation prompt back on the originating channel. Every failure path
replies on the channel — never fail silently (carried-forward constraint).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from reelarr.fulfillment.base import FulfillmentClient, FulfillmentStatus
from reelarr.intake.base import ConfirmationCandidate, InboundLink, IntakeChannel
from reelarr.models.requests import BlocklistEntry, MediaRequest, RequestEvent, RequestStatus
from reelarr.pipeline.identify import IdentificationPipeline, PipelineOutcome
from reelarr.pipeline.media import detect_platform
from reelarr.pipeline.tmdb import TmdbMatch

logger = logging.getLogger(__name__)


class RequestProcessor:
    def __init__(
        self,
        db_factory,  # callable -> Session
        pipeline: IdentificationPipeline,
        fulfillment: FulfillmentClient,
        channels: dict[str, IntakeChannel],
        fulfillment_target: str = "direct",
    ) -> None:
        self.db_factory = db_factory
        self.pipeline = pipeline
        self.fulfillment = fulfillment
        self.channels = channels
        self.fulfillment_target = fulfillment_target

    # --- intake handlers (wired via channel.on_link / on_confirmation) ------

    async def handle_link(self, link: InboundLink) -> None:
        channel = self.channels.get(link.channel)
        db: Session = self.db_factory()
        try:
            blocked = db.execute(
                select(BlocklistEntry).where(BlocklistEntry.url == link.url)
            ).scalar_one_or_none()
            if blocked is not None:
                if channel:
                    await channel.send_text(link.chat_ref, "That link is on the blocklist — skipping.")
                return

            request = MediaRequest(
                url=link.url,
                platform=detect_platform(link.url),
                source_channel=link.channel,
                source_chat_ref=link.chat_ref,
                source_user_ref=link.user_ref,
                source_message_ref=link.message_ref,
                status=RequestStatus.IDENTIFYING,
                fulfillment_target=self.fulfillment_target,
            )
            db.add(request)
            db.commit()

            if channel:
                await channel.send_text(link.chat_ref, "🔍 Looking into it...")

            try:
                result = await self.pipeline.run(link.url)
            except Exception as exc:
                logger.exception("pipeline failed for %s", link.url)
                self._fail(db, request, f"Identification failed: {exc}")
                if channel:
                    await channel.send_text(
                        link.chat_ref, "⚠️ Something went wrong identifying that clip."
                    )
                return

            request.resolved_tier = result.resolved_tier
            if result.identification:
                request.confidence = result.identification.confidence
            request.candidates = [self._match_dict(m) for m in result.candidates]
            db.add(
                RequestEvent(
                    request_id=request.id,
                    event_type="pipeline_result",
                    detail={"outcome": result.outcome.value, "tier": result.resolved_tier},
                )
            )

            if result.outcome == PipelineOutcome.UNIDENTIFIED:
                self._fail(db, request, "Could not identify the title")
                if channel:
                    await channel.send_text(
                        link.chat_ref,
                        "🤷 Couldn't identify that one — reply with the title if you know it.",
                    )
                return

            if result.outcome == PipelineOutcome.AUTO_ADD and result.match:
                await self._fulfill(db, request, result.match, channel, link.chat_ref)
                return

            # NEEDS_CONFIRMATION
            request.status = RequestStatus.PENDING_CONFIRMATION
            db.commit()
            if channel:
                candidates = [
                    ConfirmationCandidate(
                        request_id=request.id,
                        index=i + 1,
                        label=f"{m.title} ({m.year or '?'}) — {m.media_type}",
                        tmdb_id=m.tmdb_id,
                        media_type=m.media_type,
                    )
                    for i, m in enumerate(result.candidates)
                ]
                await channel.send_confirmation(
                    link.chat_ref, "Is it one of these?", candidates
                )
        finally:
            db.close()

    async def handle_confirmation(
        self, request_id: int, selected_index: int | None, chat_ref: str
    ) -> None:
        db: Session = self.db_factory()
        try:
            request = db.get(MediaRequest, request_id)
            if request is None or request.status != RequestStatus.PENDING_CONFIRMATION:
                return
            channel = self.channels.get(request.source_channel)

            if selected_index is None:
                # "None of these" — dismiss and blocklist the URL.
                request.status = RequestStatus.DISMISSED
                db.add(BlocklistEntry(url=request.url, reason="Dismissed via confirmation prompt"))
                db.commit()
                if channel:
                    await channel.send_text(chat_ref, "👍 Dismissed — I won't ask about that link again.")
                return

            candidates = request.candidates or []
            if not (1 <= selected_index <= len(candidates)):
                return
            picked = candidates[selected_index - 1]
            match = TmdbMatch(
                tmdb_id=picked["tmdb_id"],
                title=picked["title"],
                year=picked.get("year"),
                media_type=picked["media_type"],
                tvdb_id=picked.get("tvdb_id"),
                poster_url=picked.get("poster_url"),
                overview=picked.get("overview"),
            )
            await self._fulfill(db, request, match, channel, chat_ref)
        finally:
            db.close()

    # --- internals -----------------------------------------------------------

    async def _fulfill(
        self,
        db: Session,
        request: MediaRequest,
        match: TmdbMatch,
        channel: IntakeChannel | None,
        chat_ref: str,
    ) -> None:
        request.status = RequestStatus.FULFILLING
        request.title = match.title
        request.year = match.year
        request.media_type = match.media_type
        request.tmdb_id = match.tmdb_id
        request.tvdb_id = match.tvdb_id
        request.poster_url = match.poster_url
        request.overview = match.overview
        db.commit()
        try:
            result = await self.fulfillment.fulfill(match)
        except Exception as exc:
            logger.exception("fulfillment failed for request %s", request.id)
            self._fail(db, request, str(exc))
            if channel:
                await channel.send_text(chat_ref, f"⚠️ Failed to add *{match.title}*: {exc}")
            return

        if result.status == FulfillmentStatus.ALREADY_EXISTS:
            request.status = RequestStatus.ALREADY_EXISTS
        else:
            request.status = RequestStatus.FULFILLED
        db.add(
            RequestEvent(
                request_id=request.id, event_type="fulfilled", detail={"detail": result.detail}
            )
        )
        db.commit()
        if channel:
            if result.status == FulfillmentStatus.ALREADY_EXISTS:
                await channel.send_text(chat_ref, f"ℹ️ *{match.title}* — {result.detail}.")
            else:
                where = (
                    "Radarr" if match.media_type == "movie" else "Sonarr"
                ) if self.fulfillment_target == "direct" else "Overseerr"
                await channel.send_text(
                    chat_ref, f"✅ Added *{match.title} ({match.year or '?'})* to {where}."
                )
        # TODO(connect): dispatch Connect notification targets here.

    @staticmethod
    def _fail(db: Session, request: MediaRequest, error: str) -> None:
        request.status = RequestStatus.FAILED
        request.error = error
        db.commit()

    @staticmethod
    def _match_dict(m: TmdbMatch) -> dict:
        return {
            "tmdb_id": m.tmdb_id,
            "title": m.title,
            "year": m.year,
            "media_type": m.media_type,
            "tvdb_id": m.tvdb_id,
            "poster_url": m.poster_url,
            "overview": m.overview,
        }
