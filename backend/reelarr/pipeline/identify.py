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
    MULTI_TITLE_SYSTEM_PROMPT,
    Identification,
    MultiTitleExtraction,
    build_evidence_content,
    build_multi_title_content,
    build_user_content,
    detect_listicle_signal,
    no_titles,
    parse_actor_guesses,
    parse_evidence_identification,
    parse_identification,
    parse_multi_title_extraction,
)
from reelarr.pipeline.tmdb import PersonCredit, TmdbClient, TmdbMatch

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 3

# Multi-title cap (spec §5.4): a "top 50" post must not dump 50 adds into
# Radarr. Truncation is surfaced to the user, never silent (spec §1).
MAX_MULTI_TITLES = 10

# --- Tier 3 tuning ------------------------------------------------------------
VISION_FRAMES_PER_CALL = 3   # ~1000 tokens/frame at 512px; 3 fits a 4096 ctx
ACTOR_FRAME_LIMIT = 6        # per-frame actor-recognition calls, cap
MAX_ACTOR_GUESSES = 8        # distinct actor names fed to TMDB intersection
MAX_POOL_CANDIDATES = 12     # credit lookups per identification, cap
VERIFIED_MIN_HITS = 2        # character-name hits for "verified" (high)


class PipelineOutcome(str, enum.Enum):
    AUTO_ADD = "auto_add"                  # high confidence + single exact match
    NEEDS_CONFIRMATION = "needs_confirmation"  # show top-3 + "None of these"
    NEEDS_MULTI_SELECT = "needs_multi_select"  # listicle/versus — offer N titles
    UNIDENTIFIED = "unidentified"          # nothing usable — ask user for the title


@dataclass
class MultiTitleCandidate:
    """One resolved title in a multi-select offer. Confidence is per-title
    (spec §5.4: 8 certain + 2 guesses must surface that, not flatten it)."""

    match: TmdbMatch
    confidence: str  # "high" | "medium" | "low"


@dataclass
class PipelineResult:
    outcome: PipelineOutcome
    identification: Identification | None = None
    resolved_tier: str | None = None  # "metadata" | "transcript" | "frames"
    match: TmdbMatch | None = None    # populated for AUTO_ADD
    candidates: list[TmdbMatch] = field(default_factory=list)  # populated for CONFIRM
    metadata: ClipMetadata | None = None
    transcript: str | None = None
    # --- multi-title (NEEDS_MULTI_SELECT) fields (spec §5.4) ----------------
    multi_candidates: list[MultiTitleCandidate] = field(default_factory=list)
    post_type: str | None = None       # "listicle" | "versus" | ...
    stated_count: int | None = None    # the caption's own claimed count
    unresolved_titles: list[str] = field(default_factory=list)  # no TMDB match
    truncated: bool = False            # capped at max_multi_titles


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
        max_multi_titles: int = MAX_MULTI_TITLES,
    ) -> None:
        self.resolver = resolver
        self.text_llm = text_llm
        self.stt = stt
        self.tmdb = tmdb
        self.vision_llm = vision_llm
        self.enable_vision = enable_vision
        self.frame_count = frame_count
        self.max_multi_titles = max_multi_titles

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

        # Multi-title prior (spec §5.4): a caption that reads like a listicle
        # ("top 10 ...", "5 movies ...") or a versus post gets a dedicated
        # extraction pass. Hint-free captions keep today's single-title path
        # untouched — the proven Heat-with-distractors behavior must not
        # change, so the multi call is *additive*, never a replacement.
        caption = metadata.description or metadata.title
        hinted, stated_count = detect_listicle_signal(caption)
        if hinted:
            extraction = await self._extract_multi(metadata, None, stated_count)
            multi_result = await self._resolve_multi(
                extraction, stated_count, tier, metadata, None
            )
            if multi_result is not None:
                return multi_result
            # No multi offer (post classified single-subject, or <2 titles
            # survived TMDB lookup) — fall back to the normal flow, letting
            # the best extracted title improve on a blank tier-1 result.
            if extraction.titles:
                fallback = extraction.titles[0]
                if len(extraction.titles) > 1 and fallback.confidence == "high":
                    # Several subjects, but no multi-select could be built:
                    # offering just one must go through confirmation — never
                    # silently auto-add a fraction of a listicle.
                    fallback = Identification(
                        fallback.title, fallback.year, fallback.media_type, "medium"
                    )
                if self._better(fallback, ident):
                    ident = fallback

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
                if hinted:
                    # Countdown voiceovers name the titles the caption only
                    # counts ("top 10 ..." + narration) — retry with transcript.
                    extraction = await self._extract_multi(metadata, transcript, stated_count)
                    multi_result = await self._resolve_multi(
                        extraction, stated_count, "transcript", metadata, transcript
                    )
                    if multi_result is not None:
                        return multi_result

        # --- Step 3: Tier 3 — frames (feature-flagged) ---------------------
        # TODO(vision-multi): a "top 10" slideshow with no caption/audio is
        # where OCR of on-screen title cards would shine — feeding Tier 3's
        # frame descriptions into _extract_multi is the natural seam.
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
            # Last-chance multi-title pass for lists the caption regex missed
            # ("movies that will mess with your mind: ..."). Only when nothing
            # was hinted (the hinted path already tried) and only from a dead
            # end, so it can never displace a single-title identification.
            if not hinted:
                extraction = await self._extract_multi(metadata, transcript, None)
                multi_result = await self._resolve_multi(
                    extraction, extraction.stated_count, tier, metadata, transcript
                )
                if multi_result is not None:
                    return multi_result
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

    # --- Multi-title (listicle / versus) extraction (spec §5.4) --------------

    async def _extract_multi(
        self,
        metadata: ClipMetadata,
        transcript: str | None,
        stated_count: int | None,
    ) -> MultiTitleExtraction:
        """The multi pass is additive to the proven single-title flow, so its
        failures (LLM timeout on ambiguous input, garbage output) degrade to
        'no titles' and the normal flow continues — never a dead pipeline."""
        try:
            raw = await self.text_llm.complete(
                MULTI_TITLE_SYSTEM_PROMPT,
                build_multi_title_content(
                    caption=metadata.description or metadata.title,
                    hashtags=metadata.hashtags,
                    top_comments=metadata.top_comments,
                    transcript=transcript,
                    stated_count=stated_count,
                ),
            )
        except Exception:
            logger.warning("multi-title extraction failed", exc_info=True)
            return no_titles()
        return parse_multi_title_extraction(raw)

    async def _resolve_multi(
        self,
        extraction: MultiTitleExtraction,
        stated_count: int | None,
        tier: str,
        metadata: ClipMetadata,
        transcript: str | None,
    ) -> PipelineResult | None:
        """TMDB-resolve a multi-title extraction into a NEEDS_MULTI_SELECT
        result, or None if it isn't genuinely multi-title (post classified as
        single-subject, or fewer than 2 titles survive TMDB lookup) — the
        caller then continues the normal single-title flow. Multi results
        NEVER auto-add; every title goes through user selection."""
        if extraction.post_type == "single" or len(extraction.titles) < 2:
            return None

        titles = extraction.titles
        truncated = len(titles) > self.max_multi_titles
        titles = titles[: self.max_multi_titles]

        resolved: list[MultiTitleCandidate] = []
        unresolved: list[str] = []
        seen: set[tuple[str, int]] = set()
        for t in titles:
            assert t.title is not None
            try:
                matches = await self.tmdb.search_multi(t.title, year=t.year)
            except Exception:
                logger.warning("multi-title TMDB search failed for %s", t.title, exc_info=True)
                unresolved.append(t.title)
                continue
            if t.media_type:
                typed = [m for m in matches if m.media_type == t.media_type]
                matches = typed or matches
            if not matches:
                unresolved.append(t.title)
                continue
            match = matches[0]
            key = (match.media_type, match.tmdb_id)
            if key in seen:
                continue
            seen.add(key)
            # Per-title confidence: the LLM's own rating, demoted when the top
            # TMDB hit isn't an exact title match (a fuzzy hit is a guess).
            confidence = t.confidence
            if confidence == "high" and not self._is_exact_match(t, match):
                confidence = "medium"
            resolved.append(MultiTitleCandidate(match=match, confidence=confidence))

        if len(resolved) < 2:
            return None

        for item in resolved:
            m = item.match
            if m.media_type == "tv" and m.tvdb_id is None:
                try:
                    m.tvdb_id = await self.tmdb.resolve_tvdb_id(m.tmdb_id)
                except Exception:
                    logger.warning("tvdb resolution failed for tmdb:%s", m.tmdb_id, exc_info=True)

        logger.info(
            "multi-title (%s): %d resolved, %d unresolved, stated=%s, truncated=%s",
            extraction.post_type, len(resolved), len(unresolved),
            stated_count or extraction.stated_count, truncated,
        )
        return PipelineResult(
            outcome=PipelineOutcome.NEEDS_MULTI_SELECT,
            resolved_tier=tier,
            metadata=metadata,
            transcript=transcript,
            multi_candidates=resolved,
            post_type=extraction.post_type,
            # Regex prior wins over the LLM's claim — it read the caption
            # mechanically; the model sometimes rounds ("about ten").
            stated_count=stated_count or extraction.stated_count,
            unresolved_titles=unresolved,
            truncated=truncated,
        )

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
