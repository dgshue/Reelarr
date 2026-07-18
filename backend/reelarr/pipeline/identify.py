"""Identification pipeline — the 6-step flow from reelarr-spec.md §5.

1. Tier 1 (metadata):  yt-dlp metadata (caption, hashtags, top comments) -> text LLM
2. Tier 2 (transcript): if UNKNOWN or confidence < high -> audio -> STT -> re-prompt
3. Tier 3 (frames, feature-flagged): if still unresolved and enable_vision ->
   evidence accumulation + TMDB verification (see _tier3_vision below)
4. TMDB /search/multi lookup (+ TVDB external-ID resolution for TV)
5. Confidence gate: high + single exact match -> AUTO_ADD; otherwise -> CONFIRM
6. Fulfillment — performed by the caller (see reelarr.services.processor), routed
   per Settings -> Fulfillment (spec §5.5)

Tier 3 design (validated empirically against qwen2.5vl:7b + qwen3:8b):
local models cannot reliably *name* a title from visual evidence — they
hallucinate confidently — but they excel at transcribing burned-in subtitles
and recognizing well-known actors in single close-up frames. So Tier 3 never
trusts a bare LLM title guess. It gathers evidence (frame descriptions + OCR,
per-frame actor guesses, transcript), builds a candidate pool (text-LLM
candidates + TMDB filmography intersection of guessed actor pairs), then
verifies candidates against TMDB credits by character-name overlap. Only a
verified candidate gets "high" confidence; unverifiable results are capped at
"medium" (confirmation prompt), and contradicted ones are dropped entirely.

Every external dependency is injected behind an interface (MediaResolver,
TextLLMClient, SttClient, VisionLLMClient, TmdbClient) so each step is
individually mockable.
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field

from reelarr.ai.interfaces import SttClient, TextLLMClient, VisionLLMClient
from reelarr.pipeline.media import ClipMetadata, MediaResolver
from reelarr.pipeline.prompts import (
    ACTOR_RECOGNITION_SYSTEM_PROMPT,
    ACTOR_RECOGNITION_USER_PROMPT,
    EVIDENCE_IDENTIFICATION_SYSTEM_PROMPT,
    FRAME_DESCRIBE_SYSTEM_PROMPT,
    FRAME_DESCRIBE_USER_PROMPT,
    IDENTIFICATION_SYSTEM_PROMPT,
    Identification,
    build_evidence_content,
    build_user_content,
    parse_actor_guesses,
    parse_evidence_identification,
    parse_identification,
)
from reelarr.pipeline.tmdb import PersonCredit, TmdbClient, TmdbMatch

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 3

# --- Tier 3 tuning ------------------------------------------------------------
VISION_FRAMES_PER_CALL = 3   # ~1000 tokens/frame at 512px; 3 fits a 4096 ctx
ACTOR_FRAME_LIMIT = 6        # per-frame actor-recognition calls, cap
MAX_ACTOR_GUESSES = 8        # distinct actor names fed to TMDB intersection
MAX_POOL_CANDIDATES = 12     # credit lookups per identification, cap
VERIFIED_MIN_HITS = 2        # character-name hits for "verified" (high)


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
        verified_match: TmdbMatch | None = None
        if (ident.is_unknown or ident.confidence != "high") and self.enable_vision and self.vision_llm:
            try:
                tier3, tier3_match = await self._tier3_vision(url, metadata, transcript)
                if self._better(tier3, ident):
                    ident, tier = tier3, "frames"
                    verified_match = tier3_match
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
        if verified_match is not None:
            # Tier 3 already pinned the exact TMDB entry via credit
            # verification — re-searching by title could only lose it.
            matches = [verified_match]
        else:
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

    # --- Tier 3: vision evidence + TMDB verification -------------------------

    async def _tier3_vision(
        self, url: str, metadata: ClipMetadata, transcript: str | None
    ) -> tuple[Identification, TmdbMatch | None]:
        """Returns (identification, verified TMDB match | None). Never raises
        past the caller's try/except; individual vision calls degrade softly."""
        assert self.vision_llm is not None
        frames = await self.resolver.extract_frames(url, self.frame_count)
        if not frames:
            return Identification(None, None, None, "low"), None

        # 1. Describe frames + OCR burned-in text, chunked to fit the vision
        #    model's context window.
        descriptions: list[str] = []
        for i in range(0, len(frames), VISION_FRAMES_PER_CALL):
            chunk = frames[i : i + VISION_FRAMES_PER_CALL]
            try:
                desc = await self.vision_llm.complete_with_images(
                    FRAME_DESCRIBE_SYSTEM_PROMPT, FRAME_DESCRIBE_USER_PROMPT, chunk
                )
                descriptions.append(desc)
            except Exception:
                logger.warning("tier 3 describe chunk failed", exc_info=True)

        # 2. Actor recognition — one frame per call (batching degrades it).
        actor_guesses: list[str] = []
        for frame in frames[:ACTOR_FRAME_LIMIT]:
            try:
                raw = await self.vision_llm.complete_with_images(
                    ACTOR_RECOGNITION_SYSTEM_PROMPT, ACTOR_RECOGNITION_USER_PROMPT, [frame]
                )
                for name in parse_actor_guesses(raw):
                    if name not in actor_guesses:
                        actor_guesses.append(name)
            except Exception:
                logger.warning("tier 3 actor recognition failed", exc_info=True)
        actor_guesses = actor_guesses[:MAX_ACTOR_GUESSES]

        # 3. Evidence -> text LLM: candidate titles + character names heard/seen.
        evidence = build_evidence_content(
            caption=metadata.description or metadata.title,
            hashtags=metadata.hashtags,
            top_comments=metadata.top_comments,
            transcript=transcript,
            frame_descriptions=descriptions,
        )
        raw = await self.text_llm.complete(EVIDENCE_IDENTIFICATION_SYSTEM_PROMPT, evidence)
        llm_candidates, character_names = parse_evidence_identification(raw)
        # Grounding guard: verification names MUST literally occur in the
        # evidence. The same LLM call proposes candidates, and observed live,
        # a small model will emit character names that fit its own guess
        # (e.g. claim "Friends" and list Friends characters) — which would let
        # it verify its own hallucination. A mechanical word-boundary check
        # closes that loop.
        character_names = [
            n for n in character_names
            if re.search(rf"\b{re.escape(n)}\b", evidence, re.IGNORECASE)
        ]

        # 4+5. Candidate pool + verification. The pool exists solely to be
        # verified by character-name overlap, so without character names
        # (no subtitles, unusable transcript) skip the TMDB legwork entirely.
        best, best_hits, second_hits = None, 0, 0
        if character_names:
            # Filmography intersection of guessed actor pairs (two wrong
            # guesses rarely share an acting credit; two right ones often pin
            # the exact title) + the text LLM's own candidates.
            pool = await self._actor_intersection_pool(actor_guesses)
            pool += await self._resolve_llm_candidates(llm_candidates, exclude=pool)
            for candidate in pool[:MAX_POOL_CANDIDATES]:
                try:
                    characters = await self.tmdb.top_cast_characters(
                        candidate.media_type, candidate.tmdb_id
                    )
                except Exception:
                    logger.warning(
                        "tier 3 credit lookup failed for tmdb:%s", candidate.tmdb_id,
                        exc_info=True,
                    )
                    continue
                hits = self._character_hits(character_names, characters)
                if hits > best_hits:
                    best, best_hits, second_hits = candidate, hits, best_hits
                elif hits > second_hits:
                    second_hits = hits

        if best is not None and best_hits >= VERIFIED_MIN_HITS and best_hits > second_hits:
            logger.info(
                "tier 3 verified %s (%s) via %d character-name hits",
                best.title, best.year, best_hits,
            )
            return (
                Identification(
                    title=best.title, year=best.year,
                    media_type=best.media_type, confidence="high",
                ),
                best,
            )
        if best is not None and best_hits == 1:
            # Weak corroboration — surface it, but only as a confirmation.
            return (
                Identification(
                    title=best.title, year=best.year,
                    media_type=best.media_type, confidence="medium",
                ),
                best,
            )

        # No verification possible or nothing corroborated. A bare LLM title
        # guess from visual evidence is exactly where local models hallucinate,
        # so: contradicted (we had names, nothing matched) -> drop to low;
        # unverifiable (no names) -> cap at medium so it never auto-adds.
        if llm_candidates:
            top = llm_candidates[0]
            cap = "low" if character_names else "medium"
            confidence = cap if self._confidence_rank(top.confidence) > self._confidence_rank(cap) else top.confidence
            return (
                Identification(
                    title=top.title, year=top.year,
                    media_type=top.media_type, confidence=confidence,
                ),
                None,
            )
        return Identification(None, None, None, "low"), None

    async def _actor_intersection_pool(self, actor_names: list[str]) -> list[TmdbMatch]:
        """Titles where at least two guessed actors both have acting credits."""
        if len(actor_names) < 2:
            return []
        filmographies: list[dict[tuple[str, int], PersonCredit]] = []
        for name in actor_names:
            try:
                person_id = await self.tmdb.search_person(name)
                if person_id is None:
                    continue
                credits = await self.tmdb.combined_credits(person_id)
            except Exception:
                logger.warning("tier 3 filmography lookup failed for %s", name, exc_info=True)
                continue
            filmographies.append({(c.media_type, c.tmdb_id): c for c in credits})

        counts: dict[tuple[str, int], int] = {}
        by_key: dict[tuple[str, int], PersonCredit] = {}
        for filmography in filmographies:
            for key, credit in filmography.items():
                counts[key] = counts.get(key, 0) + 1
                by_key[key] = credit
        shared = [by_key[k] for k, n in counts.items() if n >= 2]
        shared.sort(key=lambda c: -c.popularity)
        return [
            TmdbMatch(
                tmdb_id=c.tmdb_id, title=c.title, year=c.year, media_type=c.media_type
            )
            for c in shared[:MAX_POOL_CANDIDATES]
        ]

    async def _resolve_llm_candidates(
        self, candidates: list[Identification], exclude: list[TmdbMatch]
    ) -> list[TmdbMatch]:
        """Resolve LLM title guesses to TMDB entries so they can be verified."""
        seen = {(m.media_type, m.tmdb_id) for m in exclude}
        resolved: list[TmdbMatch] = []
        for candidate in candidates:
            if not candidate.title:
                continue
            try:
                matches = await self.tmdb.search_multi(candidate.title, year=candidate.year)
            except Exception:
                logger.warning("tier 3 candidate search failed for %s", candidate.title,
                               exc_info=True)
                continue
            if candidate.media_type:
                typed = [m for m in matches if m.media_type == candidate.media_type]
                matches = typed or matches
            if matches and (matches[0].media_type, matches[0].tmdb_id) not in seen:
                seen.add((matches[0].media_type, matches[0].tmdb_id))
                resolved.append(matches[0])
        return resolved

    @staticmethod
    def _character_hits(character_names: list[str], characters: list[str]) -> int:
        """How many extracted character names appear among credited characters.

        Matching is prefix-tolerant for names of 4+ characters: STT and OCR
        introduce small spelling drift (whisper hears "Trip", the credit says
        "Tripp"), and nicknames are prefixes of credited names ("Jeff" /
        "Jeffrey"). Short names (<4 chars) must match exactly.
        """
        tokens: set[str] = set()
        for character in characters:
            for word in re.split(r"[^\w]+", character.lower()):
                if word:
                    tokens.add(word)

        def word_matches(w: str) -> bool:
            if w in tokens:
                return True
            if len(w) >= 4:
                return any(
                    (t.startswith(w) or w.startswith(t)) and len(t) >= 4 for t in tokens
                )
            return False

        hits = 0
        for name in character_names:
            words = [w for w in re.split(r"[^\w]+", name.lower()) if w]
            if words and all(word_matches(w) for w in words):
                hits += 1
        return hits

    @staticmethod
    def _confidence_rank(confidence: str) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(confidence, 0)

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
