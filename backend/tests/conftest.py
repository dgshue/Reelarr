"""Shared fakes — zero live services, zero paid API calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reelarr.pipeline.media import ClipMetadata


class FakeResolver:
    """MediaResolver fake with scriptable outputs."""

    def __init__(
        self,
        metadata: ClipMetadata | None = None,
        transcript_audio: Path | None = None,
        frames: list[str] | None = None,
        metadata_error: Exception | None = None,
        audio_error: Exception | None = None,
    ) -> None:
        self.metadata = metadata or ClipMetadata(platform="tiktok")
        self.transcript_audio = transcript_audio or Path("fake-audio.mp3")
        self.frames = frames or ["ZmFrZS1qcGVn"]  # "fake-jpeg"
        self.metadata_error = metadata_error
        self.audio_error = audio_error
        self.cleaned_up: list[str] = []
        self.calls: list[str] = []

    async def fetch_metadata(self, url: str) -> ClipMetadata:
        self.calls.append("metadata")
        if self.metadata_error:
            raise self.metadata_error
        return self.metadata

    async def extract_audio(self, url: str) -> Path:
        self.calls.append("audio")
        if self.audio_error:
            raise self.audio_error
        return self.transcript_audio

    async def extract_frames(self, url: str, count: int = 4) -> list[str]:
        self.calls.append("frames")
        return self.frames

    async def cleanup(self, url: str) -> None:
        self.cleaned_up.append(url)


class FakeTextLLM:
    """Returns queued responses (JSON strings) in order; repeats the last one."""

    def __init__(self, *responses: dict | str) -> None:
        self.responses = [
            r if isinstance(r, str) else json.dumps(r) for r in responses
        ] or ["{}"]
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx]


class FakeVisionLLM:
    """Routes by prompt: describe calls and actor calls get separate responses.

    Tier 3 makes two kinds of vision calls (frame description + per-frame
    actor recognition), distinguished by their system prompt.
    """

    def __init__(
        self,
        response: dict | str = "{}",
        describe_response: str | None = None,
        actor_response: dict | str | None = None,
    ) -> None:
        self.response = response if isinstance(response, str) else json.dumps(response)
        self.describe_response = describe_response
        self.actor_response = (
            actor_response if isinstance(actor_response, str | None)
            else json.dumps(actor_response)
        )
        self.calls: list[tuple[str, str, list[str]]] = []

    async def complete_with_images(self, system: str, user: str, images_b64: list[str]) -> str:
        self.calls.append((system, user, images_b64))
        if self.actor_response is not None and "actors" in system.lower():
            return self.actor_response
        if self.describe_response is not None and "describe" in system.lower():
            return self.describe_response
        return self.response


class FakeStt:
    def __init__(self, transcript: str = "", language: str | None = "en") -> None:
        self.transcript = transcript
        self.language = language
        self.calls: list[Path] = []

    async def transcribe(self, audio_path: Path):
        self.calls.append(audio_path)
        return self.transcript, self.language


class FakeTmdb:
    """TmdbClient fake returning scripted matches (+ Tier 3 person/credit data)."""

    def __init__(
        self,
        matches: list | None = None,
        tvdb_ids: dict[int, int] | None = None,
        person_ids: dict[str, int] | None = None,
        person_credits: dict[int, list] | None = None,  # person_id -> [PersonCredit]
        cast_characters: dict[tuple[str, int], list[str]] | None = None,
        matches_by_query: dict[str, list] | None = None,  # multi-title tests
    ) -> None:
        self.matches = matches or []
        self.tvdb_ids = tvdb_ids or {}
        self.person_ids = person_ids or {}
        self.person_credits = person_credits or {}
        self.cast_characters = cast_characters or {}
        self.matches_by_query = matches_by_query
        self.searches: list[tuple[str, int | None]] = []
        self.credit_lookups: list[tuple[str, int]] = []

    async def search_multi(self, query: str, year: int | None = None):
        self.searches.append((query, year))
        if self.matches_by_query is not None:
            return list(self.matches_by_query.get(query, []))
        return list(self.matches)

    async def resolve_tvdb_id(self, tmdb_id: int) -> int | None:
        return self.tvdb_ids.get(tmdb_id)

    async def search_person(self, name: str) -> int | None:
        return self.person_ids.get(name)

    async def combined_credits(self, person_id: int):
        return list(self.person_credits.get(person_id, []))

    async def top_cast_characters(self, media_type: str, tmdb_id: int, limit: int = 25):
        self.credit_lookups.append((media_type, tmdb_id))
        return list(self.cast_characters.get((media_type, tmdb_id), []))


@pytest.fixture
def anyio_backend():
    return "asyncio"
