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
