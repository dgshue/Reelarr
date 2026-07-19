"""Environment-driven configuration.

Env vars provide *defaults / bootstrap values*; anything the user edits in the
Settings UI is persisted to the database (see ``reelarr.services.settings``)
and overrides these at runtime. This mirrors how the *arr apps treat
config.xml vs UI settings.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 7979  # Reelarr's claimed port, in the *arr 4-digit tradition
    url_base: str = ""  # reverse-proxy subpath, e.g. "/reelarr"
    log_level: str = "info"
    # Override where the built web UI lives. Normally auto-detected (source
    # checkout vs. Docker image); set only for non-standard layouts.
    frontend_dist: str = ""

    # --- Storage ---
    database_url: str = "sqlite:///./data/reelarr.db"
    tmp_dir: Path = Path("./tmp")
    cookies_dir: Path = Path("./cookies")  # /config/cookies/<platform>.txt in Docker

    # --- AI (LiteLLM proxy — single OpenAI-compatible dialect, spec §5) ---
    litellm_base_url: str = "http://litellm:4000"
    litellm_api_key: str = ""
    text_model: str = "reelarr-text"        # -> ollama/qwen3:8b via litellm-config.yaml
    vision_model: str = "reelarr-vision"    # -> ollama/qwen2.5vl:7b
    stt_model: str = "reelarr-stt"          # -> Speaches faster-whisper backend
    # STT can bypass LiteLLM and hit an OpenAI-compatible STT server directly
    # (e.g. the already-deployed Speaches instance). Leave empty to use LiteLLM.
    stt_base_url: str = ""
    stt_api_key: str = ""
    enable_vision: bool = False
    ollama_url: str = "http://ollama-nvidia:11434"  # used only for "is the model pulled" checks

    @field_validator(
        "radarr_quality_profile_id", "sonarr_quality_profile_id", mode="before"
    )
    @classmethod
    def _blank_int_to_none(cls, v: object) -> object:
        """Treat an empty/whitespace env var as unset rather than a parse error.

        These are normally set from the UI after a Test call populates the
        dropdown, so `RADARR_QUALITY_PROFILE_ID=` (declared but blank) is a
        completely ordinary state — it must not crash-loop the whole app at
        startup, which is what pydantic's default int parsing would do.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # --- Metadata ---
    tmdb_api_key: str = ""

    # --- Fulfillment (spec §5.5) ---
    fulfillment_target: str = "direct"  # "direct" (Radarr/Sonarr) or "seerr"
    radarr_url: str = "http://radarr:7878"
    radarr_api_key: str = ""
    radarr_root_folder: str = ""
    radarr_quality_profile_id: int | None = None
    sonarr_url: str = "http://sonarr:8989"
    sonarr_api_key: str = ""
    sonarr_root_folder: str = ""
    sonarr_quality_profile_id: int | None = None
    seerr_url: str = "http://overseerr:5055"
    seerr_api_key: str = ""

    # --- Sources (spec §4) ---
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""  # comma-separated
    discord_bot_token: str = ""
    discord_allowed_channel_ids: str = ""
    slack_bot_token: str = ""
    slack_app_token: str = ""  # Socket Mode app-level token (xapp-...)
    slack_allowed_channel_ids: str = ""
    evolution_api_url: str = ""  # e.g. http://evolution-api:8080
    evolution_api_key: str = ""
    evolution_instance: str = "reelarr"
    whatsapp_allowed_numbers: str = ""

    # --- Pipeline limits ---
    max_video_minutes: int = 5
    max_video_height: int = 720
    frame_count: int = 6
    # Frames are downscaled before the vision call: ~1000 vision tokens per
    # frame at 512px vs ~2000 at full resolution (which overflows Ollama's
    # default 4096 context). Subtitles stay legible at 512.
    frame_width: int = 512
    # Multi-title cap (spec §5.4): a "top 50" post can't dump 50 adds into
    # Radarr. Truncation is always surfaced to the user, never silent.
    max_multi_titles: int = 10

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
