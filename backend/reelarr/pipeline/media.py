"""Media resolver — wraps yt-dlp and ffmpeg behind a mockable interface.

Mirrors the original media-resolver API contract:
- metadata:   yt-dlp --dump-json --no-download --write-comments (comments best-effort)
- transcript: download (cap 720p / ~5 min) -> ffmpeg audio extraction (STT happens
              upstream via SttClient — this layer only produces the audio file)
- frames:     scene-change frames (fallback: evenly spaced), downscaled, as
              base64 JPEGs. Downscaling matters: full-resolution frames cost
              ~2000 vision tokens each and overflow Ollama's default 4096
              context; at 512px wide they cost ~1000 each. The first/last few
              seconds are skipped (intros, outros, end cards).

Supports a cookies file at <cookies_dir>/<platform>.txt when present (needed
for Instagram; TikTok must work without cookies).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# --- Frame extraction tuning (Tier 3) ----------------------------------------
SCENE_THRESHOLD = 0.3   # ffmpeg select=gt(scene,X) — fraction of pixels changed
EDGE_SKIP_SECONDS = 3.0  # skip intro/outro (title cards, end cards, black)
MAX_SCENE_FRAMES = 24    # hard cap on scene-change frames per clip


def _evenly_subsample(items: list, count: int) -> list:
    """Pick `count` items spread evenly across the list (keeps first + last)."""
    if count <= 0 or len(items) <= count:
        return list(items)
    if count == 1:
        return [items[0]]
    idx = [round(i * (len(items) - 1) / (count - 1)) for i in range(count)]
    return [items[i] for i in dict.fromkeys(idx)]


SUPPORTED_URL_PATTERNS = (
    "instagram.com",
    "tiktok.com",
    "vm.tiktok.com",
    "facebook.com",
    "fb.watch",
)


def detect_platform(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if "facebook.com" in host or "fb.watch" in host:
        return "facebook"
    return None


@dataclass
class ClipMetadata:
    platform: str | None
    title: str | None = None
    description: str | None = None
    uploader: str | None = None
    hashtags: list[str] = field(default_factory=list)
    top_comments: list[str] = field(default_factory=list)  # max 25, sorted by likes
    duration: float | None = None


@runtime_checkable
class MediaResolver(Protocol):
    """Everything the pipeline needs from yt-dlp/ffmpeg, mockable in tests."""

    async def fetch_metadata(self, url: str) -> ClipMetadata: ...

    async def extract_audio(self, url: str) -> Path:
        """Download the clip and return the path to an extracted audio file."""
        ...

    async def extract_frames(self, url: str, count: int = 4) -> list[str]:
        """Return `count` evenly-spaced frames as base64-encoded JPEGs."""
        ...

    async def cleanup(self, url: str) -> None:
        """Delete any temp downloads for this URL (called after each request)."""
        ...


class YtDlpResolver:
    """Real implementation shelling out to yt-dlp / ffmpeg.

    Subprocess-based (rather than the yt-dlp Python API) so a yt-dlp upgrade
    is just an image rebuild, and so tests never import yt-dlp at all.
    """

    def __init__(self, tmp_dir: Path, cookies_dir: Path, max_height: int = 720,
                 max_minutes: int = 5, timeout: float = 120.0,
                 frame_width: int = 512) -> None:
        self.tmp_dir = tmp_dir
        self.cookies_dir = cookies_dir
        self.max_height = max_height
        self.max_minutes = max_minutes
        self.timeout = timeout
        self.frame_width = frame_width
        self._downloads: dict[str, Path] = {}  # url -> workdir (per-request cache)

    # --- internals -------------------------------------------------------

    def _cookie_args(self, url: str) -> list[str]:
        platform = detect_platform(url)
        if platform:
            cookie_file = self.cookies_dir / f"{platform}.txt"
            if cookie_file.is_file():
                return ["--cookies", str(cookie_file)]
        return []

    async def _run(self, *args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def _ensure_download(self, url: str) -> Path:
        """Download the video once per request lifecycle; reuse for frames."""
        if url in self._downloads and self._downloads[url].exists():
            return self._downloads[url]
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="reelarr-", dir=self.tmp_dir))
        code, _out, err = await self._run(
            "yt-dlp",
            *self._cookie_args(url),
            "-f", f"bv*[height<={self.max_height}]+ba/b[height<={self.max_height}]/b",
            "--match-filter", f"duration <= {self.max_minutes * 60}",
            "--no-playlist",
            "-o", str(workdir / "clip.%(ext)s"),
            url,
        )
        if code != 0:
            shutil.rmtree(workdir, ignore_errors=True)
            raise RuntimeError(f"yt-dlp download failed: {err.strip()[-500:]}")
        self._downloads[url] = workdir
        return workdir

    def _video_file(self, workdir: Path) -> Path:
        for f in sorted(workdir.iterdir()):
            if f.name.startswith("clip.") and f.suffix not in {".m4a", ".mp3", ".json"}:
                return f
        raise FileNotFoundError(f"no downloaded clip in {workdir}")

    # --- MediaResolver ----------------------------------------------------

    async def fetch_metadata(self, url: str) -> ClipMetadata:
        code, out, err = await self._run(
            "yt-dlp",
            *self._cookie_args(url),
            "--dump-json", "--no-download", "--write-comments",
            "--no-playlist",
            url,
        )
        if code != 0 or not out.strip():
            raise RuntimeError(f"yt-dlp metadata failed: {err.strip()[-500:]}")
        info = json.loads(out.strip().splitlines()[-1])

        comments = info.get("comments") or []  # best-effort; often unavailable
        comments = sorted(comments, key=lambda c: c.get("like_count") or 0, reverse=True)
        top_comments = [c.get("text", "") for c in comments[:25] if c.get("text")]

        # yt-dlp has no TikTok comment extractor — it reports `comment_count` in
        # the thousands but returns none, so the single strongest signal for
        # hashtag-spam captions ("#movie #fyp") was being thrown away. Fall back
        # to TikTok's own web comment API, which needs no auth. Best-effort: a
        # failure here must never fail the whole request.
        if not top_comments and (detect_platform(url) == "tiktok"):
            aweme_id = info.get("id")
            if aweme_id:
                top_comments = await self._fetch_tiktok_comments(str(aweme_id))

        description = info.get("description") or ""
        hashtags = [w for w in description.split() if w.startswith("#")]

        return ClipMetadata(
            platform=detect_platform(url) or info.get("extractor_key", "").lower() or None,
            title=info.get("title"),
            description=description or None,
            uploader=info.get("uploader") or info.get("channel"),
            hashtags=hashtags,
            top_comments=top_comments,
            duration=info.get("duration"),
        )

    async def _fetch_tiktok_comments(self, aweme_id: str, limit: int = 25) -> list[str]:
        """Top comments via TikTok's web comment API (no auth required).

        `aid=1988` is TikTok's web app id — without it the endpoint returns
        `status_code: 5` and an empty body, which is why naive scrapers see
        nothing. Returns [] on any failure; comments are an optional signal.
        """
        import httpx

        params = {"aweme_id": aweme_id, "count": 50, "cursor": 0, "aid": 1988}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://www.tiktok.com/api/comment/list/",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # network, JSON, HTTP — all non-fatal
            logger.warning("tiktok comment fetch failed for %s: %s", aweme_id, exc)
            return []

        raw = data.get("comments") or []
        raw.sort(key=lambda c: c.get("digg_count") or 0, reverse=True)
        texts = [c.get("text", "").strip() for c in raw[:limit]]
        texts = [t for t in texts if t]
        logger.info("tiktok comment fallback: %d comments for %s", len(texts), aweme_id)
        return texts

    async def extract_audio(self, url: str) -> Path:
        workdir = await self._ensure_download(url)
        audio = workdir / "audio.mp3"
        if not audio.exists():
            code, _out, err = await self._run(
                "ffmpeg", "-y", "-i", str(self._video_file(workdir)),
                "-vn", "-acodec", "libmp3lame", "-q:a", "4", str(audio),
            )
            if code != 0:
                raise RuntimeError(f"ffmpeg audio extraction failed: {err.strip()[-500:]}")
        return audio

    async def extract_frames(self, url: str, count: int = 4) -> list[str]:
        workdir = await self._ensure_download(url)
        video = self._video_file(workdir)

        # Probe duration for the edge-skip window and even-spacing fallback.
        code, out, _err = await self._run(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video),
        )
        duration = float(out.strip()) if code == 0 and out.strip() else 60.0
        skip = EDGE_SKIP_SECONDS if duration > 4 * EDGE_SKIP_SECONDS else 0.0
        start, end = skip, max(duration - skip, skip + 1.0)
        scale = f"scale={self.frame_width}:-2"

        # Preferred: scene-change detection — evenly-spaced sampling lands on
        # transitions/black frames and misses short scenes; scene cuts track
        # the actual shot structure of the clip.
        scene_dir = workdir / "scene_frames"
        scene_dir.mkdir(exist_ok=True)
        code, _out, _err = await self._run(
            "ffmpeg", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
            "-i", str(video),
            "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',{scale}",
            "-fps_mode", "vfr", "-frames:v", str(MAX_SCENE_FRAMES),
            "-q:v", "3", str(scene_dir / "scene_%03d.jpg"),
        )
        scene_files = sorted(scene_dir.glob("scene_*.jpg")) if code == 0 else []
        if len(scene_files) >= 2:
            picked = _evenly_subsample(scene_files, count)
            return [base64.b64encode(f.read_bytes()).decode() for f in picked]

        # Fallback: evenly-spaced frames inside the trimmed window (static
        # clips, single-shot videos, or scene detection failure).
        frames: list[str] = []
        for i in range(count):
            ts = start + (end - start) * (i + 0.5) / count
            frame_path = workdir / f"frame_{i}.jpg"
            code, _out, err = await self._run(
                "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video),
                "-frames:v", "1", "-vf", scale, "-q:v", "3", str(frame_path),
            )
            if code != 0:
                raise RuntimeError(f"ffmpeg frame extraction failed: {err.strip()[-500:]}")
            frames.append(base64.b64encode(frame_path.read_bytes()).decode())
        return frames

    async def cleanup(self, url: str) -> None:
        workdir = self._downloads.pop(url, None)
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
