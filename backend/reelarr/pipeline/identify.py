"""Identification pipeline — the 6-step flow from reelarr-spec.md §5.

1. Tier 1 (metadata):  yt-dlp metadata (caption, hashtags, top comments) -> text LLM
2. Tier 2 (transcript): if UNKNOWN or confidence < high -> audio -> STT -> re-prompt
3. Tier 3 (frames, feature-flagged): if still unresolved and enable_vision -> vision LLM
4. TMDB /search/multi lookup (+ TVDB external-ID resolution for TV)
5. Confidence gate: high + single exact match -> AUTO_ADD; otherwise -> CONFIRM
6. Fulfillment — performed by the caller (see reelarr.services.processor), routed
   per Settings -> Fulfillment (spec §5.5)

Every external dependency is injected behind an interface (MediaResolver,
TextLLMClient, SttClient, VisionLLMClient, TmdbClient) so each step is
individually mockable.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

from reelarr.ai.interfaces import SttClient, TextLLMClient, VisionLLMClient
from reelarr.pipeline.media import ClipMetadata, MediaResolver
from reelarr.pipeline.prompts import (
    IDENTIFICATION_SYSTEM_PROMPT,
    Identification,
    build_user_content,
    parse_identification,
)
from reelarr.pipeline.tmdb import TmdbClient, TmdbMatch

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 3


class PipelineOutcome(str, enum.Enum):
    AUTO_ADD = "auto_add"                  # high confidence + single exact match
    NEEDS_CONFIRMATION = "needs_confirmation"  # show top-3 + "None of these"
    UNIDENTIFIED = "unidentified"          # nothing usable — ask user for the title


@dataclass
class PipelineResult:
    outcome: PipelineOutcome
    identification: Identification | None = None
    resolved_tier: str | None = None  # "metadata" | "transcript" | "frames"
    match: TmdbMatch | None = None    # populated for AUTO_ADD
    candidates: list[TmdbMatch] = field(default_factory=list)  # populated for CONFIRM
    metadata: ClipMetadata | None = None
    transcript: str | None = None


class IdentificationPipeline:
    def __init__(
        self,
        resolver: MediaResolver,
        text_llm: TextLLMClient,
        stt: SttClient,
        tmdb: TmdbClient,
        vision_llm: VisionLLMClient | None = None,
        enable_vision: bool = False,
        frame_count: int = 4,
    ) -> None:
        self.resolver = resolver
        self.text_llm = text_llm
        self.stt = stt
        self.tmdb = tmdb
        self.vision_llm = vision_llm
        self.enable_vision = enable_vision
        self.frame_count = frame_count

    async def run(self, url: str) -> PipelineResult:
        try:
            return await self._run(url)
        finally:
            # Temp downloads deleted after each request (spec constraint).
            try:
                await self.resolver.cleanup(url)
            except Exception:  # pragma: no cover - best-effort cleanup
                logger.warning("cleanup failed for %s", url, exc_info=True)

    async def _run(self, url: str) -> PipelineResult:
        # --- Step 1: Tier 1 — metadata -----------------------------------
        metadata = await self.resolver.fetch_metadata(url)
        ident = await self._identify_from_text(metadata=metadata)
        tier = "metadata"
        transcript: str | None = None

        # --- Step 2: Tier 2 — transcript ----------------------------------
        if ident.is_unknown or ident.confidence != "high":
            try:
                audio_path = await self.resolver.extract_audio(url)
                transcript, _lang = await self.stt.transcribe(audio_path)
            except Exception:
                logger.warning("tier 2 (transcript) failed for %s", url, exc_info=True)
                transcript = None
            if transcript:
                tier2 = await self._identify_from_text(metadata=metadata, transcript=transcript)
                if self._better(tier2, ident):
                    ident, tier = tier2, "transcript"

        # --- Step 3: Tier 3 — frames (feature-flagged) ---------------------
        if (ident.is_unknown or ident.confidence != "high") and self.enable_vision and self.vision_llm:
            try:
                frames = await self.resolver.extract_frames(url, self.frame_count)
                raw = await self.vision_llm.complete_with_images(
                    IDENTIFICATION_SYSTEM_PROMPT,
                    build_user_content(
                        caption=metadata.description or metadata.title,
                        hashtags=metadata.hashtags,
                        top_comments=metadata.top_comments,
                        transcript=transcript,
                    ),
                    frames,
                )
                tier3 = parse_identification(raw)
                if self._better(tier3, ident):
                    ident, tier = tier3, "frames"
            except Exception:
                logger.warning("tier 3 (frames) failed for %s", url, exc_info=True)

        if ident.is_unknown:
            return PipelineResult(
                outcome=PipelineOutcome.UNIDENTIFIED,
                identification=ident,
                metadata=metadata,
                transcript=transcript,
            )

        # --- Step 4: TMDB lookup -------------------------------------------
        assert ident.title is not None
        matches = await self.tmdb.search_multi(ident.title, year=ident.year)
        if ident.media_type:
            typed = [m for m in matches if m.media_type == ident.media_type]
            if typed:
                matches = typed
        if not matches:
            return PipelineResult(
                outcome=PipelineOutcome.UNIDENTIFIED,
                identification=ident,
                resolved_tier=tier,
                metadata=metadata,
                transcript=transcript,
            )

        # Resolve tvdbId for TV candidates up front — Sonarr requires it.
        for m in matches[:MAX_CANDIDATES]:
            if m.media_type == "tv" and m.tvdb_id is None:
                try:
                    m.tvdb_id = await self.tmdb.resolve_tvdb_id(m.tmdb_id)
                except Exception:
                    logger.warning("tvdb resolution failed for tmdb:%s", m.tmdb_id, exc_info=True)

        # --- Step 5: identification-confidence gate ------------------------
        top = matches[0]
        exact_single = self._is_exact_match(ident, top) and (
            len(matches) == 1 or not self._is_exact_match(ident, matches[1])
        )
        if ident.confidence == "high" and exact_single:
            return PipelineResult(
                outcome=PipelineOutcome.AUTO_ADD,
                identification=ident,
                resolved_tier=tier,
                match=top,
                candidates=matches[:MAX_CANDIDATES],
                metadata=metadata,
                transcript=transcript,
            )

        return PipelineResult(
            outcome=PipelineOutcome.NEEDS_CONFIRMATION,
            identification=ident,
            resolved_tier=tier,
            candidates=matches[:MAX_CANDIDATES],
            metadata=metadata,
            transcript=transcript,
        )

    # --- helpers -----------------------------------------------------------

    async def _identify_from_text(
        self, metadata: ClipMetadata, transcript: str | None = None
    ) -> Identification:
        raw = await self.text_llm.complete(
            IDENTIFICATION_SYSTEM_PROMPT,
            build_user_content(
                caption=metadata.description or metadata.title,
                hashtags=metadata.hashtags,
                top_comments=metadata.top_comments,
                transcript=transcript,
            ),
        )
        return parse_identification(raw)

    @staticmethod
    def _better(new: Identification, old: Identification) -> bool:
        rank = {"low": 0, "medium": 1, "high": 2}
        if old.is_unknown:
            return not new.is_unknown
        if new.is_unknown:
            return False
        return rank[new.confidence] > rank[old.confidence]

    @staticmethod
    def _is_exact_match(ident: Identification, match: TmdbMatch) -> bool:
        if not ident.title:
            return False
        title_eq = ident.title.strip().lower() == match.title.strip().lower()
        year_ok = ident.year is None or match.year is None or ident.year == match.year
        return title_eq and year_ok
