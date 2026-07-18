"""OpenAI-dialect implementations of the AI interfaces, pointed at LiteLLM.

Endpoints used:
- POST {base}/v1/chat/completions   (text + vision content blocks)
- POST {base}/v1/audio/transcriptions
- GET  {base}/v1/models             (Test button: model presence check)
"""

from __future__ import annotations

from pathlib import Path

import httpx

DEFAULT_TIMEOUT = 120.0


class _OpenAICompatBase:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @property
    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _url(self, path: str) -> str:
        base = self.base_url
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return f"{base}{path}"

    async def list_models(self) -> list[str]:
        resp = await self._client.get(self._url("/models"), headers=self._headers)
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAICompatTextClient(_OpenAICompatBase):
    async def complete(self, system: str, user: str) -> str:
        resp = await self._client.post(
            self._url("/chat/completions"),
            headers=self._headers,
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class OpenAICompatVisionClient(_OpenAICompatBase):
    async def complete_with_images(self, system: str, user: str, images_b64: list[str]) -> str:
        content: list[dict] = [{"type": "text", "text": user}]
        for b64 in images_b64:
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            )
        resp = await self._client.post(
            self._url("/chat/completions"),
            headers=self._headers,
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                "temperature": 0.1,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class OpenAICompatSttClient(_OpenAICompatBase):
    async def transcribe(self, audio_path: Path) -> tuple[str, str | None]:
        with open(audio_path, "rb") as fh:
            resp = await self._client.post(
                self._url("/audio/transcriptions"),
                headers=self._headers,
                data={"model": self.model, "response_format": "json"},
                files={"file": (audio_path.name, fh, "audio/mpeg")},
            )
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("text", ""), payload.get("language")
