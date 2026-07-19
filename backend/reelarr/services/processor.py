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
from reelarr.intake.base import (
    ConfirmationCandidate,
    InboundLink,
    IntakeChannel,
    MultiSelectOption,
)
from reelarr.models.requests import BlocklistEntry, MediaRequest, RequestEvent, RequestStatus
from reelarr.pipeline.identify import IdentificationPipeline, PipelineOutcome, PipelineResult
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

            if result.outcome == PipelineOutcome.NEEDS_MULTI_SELECT:
                # Listicle / versus post (spec §5.4). Candidates — including
                # per-title confidence and selection state — persist on the
                # request row so toggles survive a restart.
                request.status = RequestStatus.PENDING_CONFIRMATION
                request.candidates = [
                    self._match_dict(c.match) | {"confidence": c.confidence, "selected": False}
                    for c in result.multi_candidates
                ]
                db.commit()
                if channel:
                    await channel.send_multi_select(
                        link.chat_ref,
                        self._multi_prompt(result),
                        self._multi_options(request),
                    )
                return

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

    async def handle_multi_select(
        self,
        request_id: int,
        action: str,
        indexes: list[int] | None,
        chat_ref: str,
        message_ref: str | None,
    ) -> None:
        """Multi-select interaction (spec §5.4). Selection state is read from
        and written to the request row's candidates JSON — never held in
        memory — so an in-flight selection survives a restart."""
        db: Session = self.db_factory()
        try:
            request = db.get(MediaRequest, request_id)
            if request is None or request.status != RequestStatus.PENDING_CONFIRMATION:
                return  # already confirmed/dismissed — stale button tap
            channel = self.channels.get(request.source_channel)
            # Fresh dicts: in-place mutation of a JSON column isn't change-tracked.
            candidates = [dict(c) for c in (request.candidates or [])]

            if action == "toggle" and indexes:
                i = indexes[0]
                if not (1 <= i <= len(candidates)):
                    return
                candidates[i - 1]["selected"] = not candidates[i - 1].get("selected", False)
                request.candidates = candidates
                db.commit()
                if channel:
                    await channel.update_multi_select(
                        chat_ref, message_ref, self._multi_options(request)
                    )
                return

            if action == "replace" and indexes is not None:
                # Whole-set submission: Discord native select / WhatsApp "1,3,5".
                for i, candidate in enumerate(candidates, start=1):
                    candidate["selected"] = i in indexes
                request.candidates = candidates
                db.commit()
                return

            if action == "all":
                # "Add all" writes several items at once — re-prompt first.
                if channel:
                    await channel.update_multi_select(
                        chat_ref, message_ref, self._multi_options(request), confirm_all=True
                    )
                return

            if action == "back":
                if channel:
                    await channel.update_multi_select(
                        chat_ref, message_ref, self._multi_options(request)
                    )
                return

            if action == "none":
                request.status = RequestStatus.DISMISSED
                db.add(BlocklistEntry(url=request.url, reason="Dismissed via multi-select prompt"))
                db.commit()
                if channel:
                    await channel.send_text(
                        chat_ref, "👍 Dismissed — I won't ask about that link again."
                    )
                return

            if action == "confirm_all":
                for candidate in candidates:
                    candidate["selected"] = True
                request.candidates = candidates
                db.commit()
            elif action != "confirm":
                return

            picked = [c for c in candidates if c.get("selected")]
            if not picked:
                if channel:
                    await channel.send_text(
                        chat_ref,
                        "Nothing selected yet — tap a title to select it, or ❌ None of these.",
                    )
                return
            await self._fulfill_multi(db, request, picked, channel, chat_ref)
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
        line = await self._fulfill_row(db, request, match)
        if channel:
            await channel.send_text(chat_ref, line)
        # TODO(connect): dispatch Connect notification targets here.

    async def _fulfill_row(self, db: Session, request: MediaRequest, match: TmdbMatch) -> str:
        """Fulfill one request row; returns the user-facing result line so
        multi-title adds can send one summary instead of N messages."""
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
            return f"⚠️ Failed to add *{match.title}*: {exc}"

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
        if result.status == FulfillmentStatus.ALREADY_EXISTS:
            return f"ℹ️ *{match.title}* — {result.detail}."
        where = (
            "Radarr" if match.media_type == "movie" else "Sonarr"
        ) if self.fulfillment_target == "direct" else "Overseerr"
        return f"✅ Added *{match.title} ({match.year or '?'})* to {where}."

    async def _fulfill_multi(
        self,
        db: Session,
        request: MediaRequest,
        picked: list[dict],
        channel: IntakeChannel | None,
        chat_ref: str,
    ) -> None:
        """Fulfill every selected title. The first selection reuses the parent
        request row (which ran the pipeline); each further title becomes its
        own sibling row so Library and Activity stay one-row-per-title."""
        rows: list[MediaRequest] = [request]
        for _ in picked[1:]:
            sibling = MediaRequest(
                url=request.url,
                platform=request.platform,
                source_channel=request.source_channel,
                source_chat_ref=request.source_chat_ref,
                source_user_ref=request.source_user_ref,
                source_message_ref=request.source_message_ref,
                status=RequestStatus.FULFILLING,
                resolved_tier=request.resolved_tier,
                fulfillment_target=request.fulfillment_target,
            )
            db.add(sibling)
            rows.append(sibling)
        db.commit()

        lines: list[str] = []
        for row, candidate in zip(rows, picked):
            row.confidence = candidate.get("confidence")
            match = TmdbMatch(
                tmdb_id=candidate["tmdb_id"],
                title=candidate["title"],
                year=candidate.get("year"),
                media_type=candidate["media_type"],
                tvdb_id=candidate.get("tvdb_id"),
                poster_url=candidate.get("poster_url"),
                overview=candidate.get("overview"),
            )
            lines.append(await self._fulfill_row(db, row, match))
        if channel:
            text = "\n".join(lines)
            if len(lines) > 1:
                text = f"Added {len(lines)} titles:\n{text}"
            await channel.send_text(chat_ref, text)
        # TODO(connect): dispatch Connect notification targets here.

    _CONFIDENCE_MARKER = {"medium": " (unsure)", "low": " (guess)"}

    def _multi_options(self, request: MediaRequest) -> list[MultiSelectOption]:
        """Rebuild the option list from the request row — the DB is the single
        source of truth for selection state (spec §5.4)."""
        options: list[MultiSelectOption] = []
        for i, c in enumerate(request.candidates or [], start=1):
            confidence = c.get("confidence", "high")
            label = (
                f"{c['title']} ({c.get('year') or '?'}) — {c['media_type']}"
                f"{self._CONFIDENCE_MARKER.get(confidence, '')}"
            )
            options.append(
                MultiSelectOption(
                    request_id=request.id,
                    index=i,
                    label=label,
                    tmdb_id=c["tmdb_id"],
                    media_type=c["media_type"],
                    confidence=confidence,
                    selected=bool(c.get("selected")),
                )
            )
        return options

    @staticmethod
    def _multi_prompt(result: PipelineResult) -> str:
        """Prompt text for a multi-title offer. Shortfalls against the
        caption's stated count and cap truncation are said plainly — never
        silently return fewer than the post claims (spec §5.4, §1)."""
        found = len(result.multi_candidates)
        kind = "a versus post" if result.post_type == "versus" else "a list"
        lines = [f"This looks like {kind} — I identified {found} titles."]
        if result.stated_count and result.stated_count > found:
            lines[0] = (
                f"This looks like {kind} — the post claims {result.stated_count} titles, "
                f"I identified {found}."
            )
        if result.truncated:
            lines.append(f"Only the first {found} are shown (capped).")
        if result.unresolved_titles:
            lines.append("Couldn't match: " + ", ".join(result.unresolved_titles) + ".")
        lines.append("Select the ones you want to add.")  # channel-neutral wording
        return "\n".join(lines)

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
