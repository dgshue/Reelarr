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
