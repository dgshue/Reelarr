"""Thin AI provider interfaces (spec §5).

All production implementations speak the OpenAI-compatible dialect against a
single LiteLLM proxy endpoint, but each component keeps its own interface so
any one of them could bypass LiteLLM later without a rewrite — and so tests
can substitute fakes trivially.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TextLLMClient(Protocol):
    async def complete(self, system: str, user: str) -> str:
        """Return the raw assistant text for a system+user chat completion."""
        ...


@runtime_checkable
class VisionLLMClient(Protocol):
    async def complete_with_images(self, system: str, user: str, images_b64: list[str]) -> str:
        """Chat completion with base64 JPEG content blocks (OpenAI vision format)."""
        ...


@runtime_checkable
class SttClient(Protocol):
    async def transcribe(self, audio_path: Path) -> tuple[str, str | None]:
        """Transcribe an audio file. Returns (transcript, language|None)."""
        ...
