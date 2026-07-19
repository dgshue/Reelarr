"""LLM identification prompt + JSON contract.

Carried forward verbatim from media-share-pipeline-spec.md §"LLM identification
prompt (Tier 1/2)" — do not tweak wording without re-testing local models.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

IDENTIFICATION_SYSTEM_PROMPT = (
    "You identify which movie or TV show a social media video clip is from. "
    'Respond ONLY with JSON: { "title": string|null, "year": number|null, '
    '"type": "movie"|"tv"|null, "confidence": "high"|"medium"|"low" }. '
    "Use null title if you cannot identify it. Captions and comments frequently "
    "name the title directly — weight explicit mentions heavily. Ignore hashtag "
    "spam like #fyp #movie #film unless a specific title is named."
)

# --- Tier 3 (vision) prompts --------------------------------------------------
# Empirically (validated against qwen2.5vl:7b via Ollama), asking the local
# vision model "which movie is this" directly hallucinates; what it does
# reliably is (a) transcribe burned-in subtitle text near-perfectly and
# (b) recognize well-known actors from single close-up frames. Tier 3
# therefore collects *evidence* (OCR text, actor guesses, scene description)
# and leaves naming/verification to the text LLM + TMDB.

FRAME_DESCRIBE_SYSTEM_PROMPT = (
    "You describe frames from a social media video clip so a film expert can "
    "identify which movie or TV show they are from. Report only what you can "
    "actually see:\n"
    "1. ON-SCREEN TEXT: for EACH frame, transcribe ALL burned-in text exactly — "
    "subtitles, captions, title cards, watermarks — and note its language. "
    "Check every frame separately; subtitles change between frames.\n"
    "2. PEOPLE: for each distinct person, appearance in detail (age, hair, "
    "facial features, clothing).\n"
    "3. SETTING & ERA: location, props, vehicles, clothing style, approximate "
    "decade, color grading.\n"
    "4. Distinctive details (logos, signage, uniforms, weapons, creatures).\n"
    "Do NOT guess the movie title. Be concise but complete."
)

FRAME_DESCRIBE_USER_PROMPT = "Describe these frames from one video clip."

# Single-frame calls only: batching multiple frames measurably degrades
# recognition accuracy on qwen2.5vl:7b (it blends faces across frames).
ACTOR_RECOGNITION_SYSTEM_PROMPT = (
    "You are a film-industry expert with encyclopedic knowledge of actors. "
    "This is a frame from a professional movie or TV production; the people in "
    "it are professional actors. List which well-known actors each "
    "clearly-visible person most resembles. Respond ONLY with JSON: "
    '{ "actors": [ { "name": string, "confidence": "certain"|"likely"|"unsure" } ] }. '
    "Use an empty list if you cannot tell."
)

ACTOR_RECOGNITION_USER_PROMPT = "Which well-known actors do these people resemble?"

# --- Multi-title (listicle / versus) prompt (spec §5.4) ------------------------
# A large share of film content is countdowns ("top 10 horror films"), lists
# ("5 mind-bending movies: ..."), or versus posts. The single-title contract
# returns null/low on those (measured, 2026-07-18). This prompt classifies the
# *post type* first — the critical subtlety is separating "several films are
# the subject" (listicle) from "several films are merely mentioned" (a
# single-subject post whose comments name comparisons), which must keep
# resolving to the one subject.

MULTI_TITLE_SYSTEM_PROMPT = (
    "You analyze a social media video post about movies or TV shows. "
    "First classify the post:\n"
    '- "single": the post is about ONE title. Other titles mentioned in '
    "comments or as comparisons are NOT subjects.\n"
    '- "listicle": a ranking, countdown, or list ("top 10 ...", "5 movies '
    'that ...") where several titles are each a subject.\n'
    '- "versus": two or more titles compared head-to-head.\n'
    'Respond ONLY with JSON: { "post_type": "single"|"listicle"|"versus"|"unknown", '
    '"stated_count": number|null, "titles": [ { "title": string, '
    '"year": number|null, "type": "movie"|"tv"|null, '
    '"confidence": "high"|"medium"|"low" } ] }. '
    'stated_count: how many titles the caption itself claims (10 for "top 10"), '
    "null if it does not say. titles: every title that is a SUBJECT of the "
    'post, best first — exactly one for "single", each compared title for '
    '"versus". Only include titles actually named or clearly identifiable from '
    "the material. NEVER invent or pad titles to reach stated_count — fewer "
    "correct titles is better than a padded list. Ignore hashtag spam like "
    "#fyp #movie #film."
)

_VALID_POST_TYPES = {"single", "listicle", "versus", "unknown"}

# Caption-count prior (spec §5.4: "use the caption's own stated count").
# Word-numbers cover "five movies you need to watch"-style captions.
_WORD_NUMBERS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "twenty": 20,
}
_NUM = r"(\d{1,3}|" + "|".join(_WORD_NUMBERS) + r")"
_LISTICLE_PATTERNS = [
    # "top 10 horror films", "my top5"
    re.compile(rf"\btop\s{{0,3}}{_NUM}\b", re.IGNORECASE),
    # "5 mind-bending movies", "five underrated sci-fi films"
    re.compile(
        rf"\b{_NUM}\s+(?:[\w'-]+\s+){{0,3}}?(?:movies|films|shows|series)\b",
        re.IGNORECASE,
    ),
    # "ranking every A24 horror movie", "ranked worst to best"
    re.compile(r"\brank(?:ing|ed)\b", re.IGNORECASE),
    re.compile(r"\bcountdown\b", re.IGNORECASE),
]
_VERSUS_PATTERN = re.compile(r"\bvs\.?\b|\bversus\b", re.IGNORECASE)


def detect_listicle_signal(text: str | None) -> tuple[bool, int | None]:
    """Cheap regex prior over the caption: (looks multi-title, stated count).

    Deliberately conservative — a miss only costs the fast path (the pipeline
    still attempts multi-title extraction as a last resort before giving up),
    while a false positive costs one extra LLM call whose "single"
    classification falls back to the normal flow. Plain single-subject
    captions ("This scene from Heat is unmatched") must not trigger.
    """
    if not text:
        return False, None
    count: int | None = None
    hinted = False
    for pattern in _LISTICLE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        hinted = True
        if m.groups() and m.group(1) and count is None:
            token = m.group(1).lower()
            count = int(token) if token.isdigit() else _WORD_NUMBERS.get(token)
    if _VERSUS_PATTERN.search(text):
        hinted = True
    if count is not None and not (2 <= count <= 100):
        count = None  # "1 movie" / absurd numbers: no useful prior
    return hinted, count


EVIDENCE_IDENTIFICATION_SYSTEM_PROMPT = (
    "You identify which movie or TV show a social media video clip is from, "
    "using evidence gathered from the clip: caption, top comments, audio "
    "transcript, and a visual description of frames including any burned-in "
    "subtitle text. Character names appearing in subtitles or dialogue are the "
    "strongest signal. Respond ONLY with JSON: "
    '{ "candidates": [ { "title": string, "year": number|null, '
    '"type": "movie"|"tv", "confidence": "high"|"medium"|"low" } ], '
    '"character_names": [string] }. '
    "candidates: up to 3, best first, empty list if you cannot identify it — "
    "never invent a title just to fill the list. character_names: proper names "
    "of characters that appear in the subtitles, dialogue, or transcript "
    "(people spoken to or about), empty list if none."
)

_VALID_CONFIDENCE = {"high", "medium", "low"}
_VALID_TYPES = {"movie", "tv"}


@dataclass
class Identification:
    title: str | None
    year: int | None
    media_type: str | None  # "movie" | "tv" | None
    confidence: str  # "high" | "medium" | "low"

    @property
    def is_unknown(self) -> bool:
        return not self.title


UNKNOWN = Identification(title=None, year=None, media_type=None, confidence="low")


def build_user_content(
    *,
    caption: str | None = None,
    hashtags: list[str] | None = None,
    top_comments: list[str] | None = None,
    transcript: str | None = None,
) -> str:
    """Clearly-labeled user content, per the original spec."""
    parts: list[str] = []
    if caption:
        parts.append(f"CAPTION:\n{caption}")
    if hashtags:
        parts.append("HASHTAGS:\n" + " ".join(hashtags))
    if top_comments:
        parts.append("TOP COMMENTS:\n" + "\n".join(f"- {c}" for c in top_comments))
    if transcript:
        parts.append(f"AUDIO TRANSCRIPT:\n{transcript}")
    if not parts:
        parts.append("(no metadata available)")
    return "\n\n".join(parts)


def build_evidence_content(
    *,
    caption: str | None = None,
    hashtags: list[str] | None = None,
    top_comments: list[str] | None = None,
    transcript: str | None = None,
    frame_descriptions: list[str] | None = None,
) -> str:
    """All accumulated evidence for the Tier 3 identification call."""
    parts: list[str] = []
    if caption:
        parts.append(f"CAPTION:\n{caption}")
    if hashtags:
        parts.append("HASHTAGS:\n" + " ".join(hashtags))
    if top_comments:
        parts.append("TOP COMMENTS:\n" + "\n".join(f"- {c}" for c in top_comments))
    if transcript:
        parts.append(f"AUDIO TRANSCRIPT:\n{transcript}")
    if frame_descriptions:
        joined = "\n\n".join(d.strip() for d in frame_descriptions if d and d.strip())
        if joined:
            parts.append(f"VISUAL DESCRIPTION OF FRAMES (including on-screen text):\n{joined}")
    if not parts:
        parts.append("(no evidence available)")
    return "\n\n".join(parts)


def _extract_json(raw: str) -> dict | None:
    """Shared defensive JSON extraction: think-blocks, fences, prose wrapping."""
    if not raw:
        return None
    # qwen3-style reasoning models may emit <think>...</think> before the JSON.
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if not brace:
            return None
        text = brace.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_identification(raw: str) -> Identification:
    """Defensive parse: strip markdown fences, fall back to UNKNOWN on failure."""
    if not raw:
        return UNKNOWN
    text = raw.strip()
    # Strip ```json ... ``` / ``` ... ``` fences.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Some models wrap JSON in prose — grab the first {...} block.
    if not text.startswith("{"):
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if not brace:
            return UNKNOWN
        text = brace.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return UNKNOWN
    if not isinstance(data, dict):
        return UNKNOWN

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        title = None

    year = data.get("year")
    if isinstance(year, str) and year.isdigit():
        year = int(year)
    if not isinstance(year, int):
        year = None

    media_type = data.get("type")
    if media_type not in _VALID_TYPES:
        media_type = None

    confidence = data.get("confidence")
    if confidence not in _VALID_CONFIDENCE:
        confidence = "low"

    return Identification(title=title, year=year, media_type=media_type, confidence=confidence)


def parse_actor_guesses(raw: str, include_unsure: bool = False) -> list[str]:
    """Parse the actor-recognition JSON. Returns actor names, best-effort."""
    data = _extract_json(raw)
    if not data:
        return []
    actors = data.get("actors")
    if not isinstance(actors, list):
        return []
    names: list[str] = []
    allowed = {"certain", "likely"} | ({"unsure"} if include_unsure else set())
    for entry in actors:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        confidence = entry.get("confidence", "likely")
        if isinstance(name, str) and name.strip() and confidence in allowed:
            names.append(name.strip())
    return names


def parse_evidence_identification(raw: str) -> tuple[list[Identification], list[str]]:
    """Parse the Tier 3 evidence call: (candidates best-first, character names).

    Falls back to ([], []) on garbage — the pipeline treats that as UNKNOWN.
    """
    data = _extract_json(raw)
    if not data:
        return [], []

    candidates: list[Identification] = []
    raw_candidates = data.get("candidates")
    if isinstance(raw_candidates, list):
        for entry in raw_candidates:
            if not isinstance(entry, dict):
                continue
            ident = parse_identification(json.dumps(entry))
            if not ident.is_unknown:
                candidates.append(ident)

    character_names: list[str] = []
    raw_names = data.get("character_names")
    if isinstance(raw_names, list):
        for name in raw_names:
            if isinstance(name, str):
                cleaned = name.strip()
                if cleaned.lower().endswith("'s"):  # possessive: "Tripp's" -> "Tripp"
                    cleaned = cleaned[:-2]
                if cleaned and cleaned not in character_names:
                    character_names.append(cleaned)

    return candidates[:3], character_names


# --- Multi-title extraction (spec §5.4) ----------------------------------------


@dataclass
class MultiTitleExtraction:
    post_type: str  # "single" | "listicle" | "versus" | "unknown"
    stated_count: int | None
    titles: list[Identification]  # best first, unknowns dropped


def no_titles() -> MultiTitleExtraction:
    """Fresh empty extraction (not a shared singleton — the list is mutable)."""
    return MultiTitleExtraction(post_type="unknown", stated_count=None, titles=[])


def build_multi_title_content(
    *,
    caption: str | None = None,
    hashtags: list[str] | None = None,
    top_comments: list[str] | None = None,
    transcript: str | None = None,
    stated_count: int | None = None,
) -> str:
    """Same labeled sections as the single-title call, plus the caption-count
    prior as an explicit expectation the model is told not to pad toward."""
    content = build_user_content(
        caption=caption,
        hashtags=hashtags,
        top_comments=top_comments,
        transcript=transcript,
    )
    if stated_count is not None:
        content += (
            f"\n\nNOTE: the caption claims this post covers {stated_count} titles. "
            "If you can identify fewer, that is fine — do not invent titles to "
            "reach the count."
        )
    return content


def parse_multi_title_extraction(raw: str) -> MultiTitleExtraction:
    """Defensive parse of the multi-title call. Falls back to an empty
    extraction on garbage — the pipeline then continues the single-title flow."""
    data = _extract_json(raw)
    if not data:
        return no_titles()

    post_type = data.get("post_type")
    if post_type not in _VALID_POST_TYPES:
        post_type = "unknown"

    stated_count = data.get("stated_count")
    if isinstance(stated_count, str) and stated_count.isdigit():
        stated_count = int(stated_count)
    if not isinstance(stated_count, int) or not (2 <= stated_count <= 100):
        stated_count = None

    titles: list[Identification] = []
    seen: set[tuple[str, int | None]] = set()
    raw_titles = data.get("titles")
    if isinstance(raw_titles, list):
        for entry in raw_titles[:25]:  # sanity bound before the pipeline's cap
            if not isinstance(entry, dict):
                continue
            ident = parse_identification(json.dumps(entry))
            if ident.is_unknown:
                continue
            assert ident.title is not None
            key = (ident.title.strip().lower(), ident.year)
            if key in seen:
                continue
            seen.add(key)
            titles.append(ident)

    return MultiTitleExtraction(post_type=post_type, stated_count=stated_count, titles=titles)
