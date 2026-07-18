"""Settings service — DB values override env defaults.

Each Settings nav section has a pydantic schema; the UI PUTs a payload which
is validated and stored as one SettingsSection row. Reads merge the stored
payload over the env-derived defaults from reelarr.config.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from reelarr.config import get_config
from reelarr.models.settings import SettingsSection

# --- Section schemas ---------------------------------------------------------


class FulfillmentSettings(BaseModel):
    target: str = "direct"  # "direct" | "seerr"
    radarr_url: str = ""
    radarr_api_key: str = ""
    radarr_root_folder: str = ""
    radarr_quality_profile_id: int | None = None
    radarr_tags: list[int] = Field(default_factory=list)
    sonarr_url: str = ""
    sonarr_api_key: str = ""
    sonarr_root_folder: str = ""
    sonarr_quality_profile_id: int | None = None
    sonarr_tags: list[int] = Field(default_factory=list)
    seerr_url: str = ""
    seerr_api_key: str = ""


class IdentificationSettings(BaseModel):
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    text_model: str = ""
    vision_model: str = ""
    stt_model: str = ""
    stt_base_url: str = ""
    stt_api_key: str = ""
    enable_vision: bool = False
    frame_count: int = 4
    max_video_minutes: int = 5
    max_video_height: int = 720


class MetadataSettings(BaseModel):
    tmdb_api_key: str = ""


class ConnectTarget(BaseModel):
    """One outbound notification target (Settings -> Connect, spec §6)."""

    name: str
    # discord | webhook | pushover | slack | ntfy | gotify | apprise | telegram
    target_type: str
    enabled: bool = True
    # type-specific: webhook URL, tokens, topic, apprise URL, etc.
    config: dict = Field(default_factory=dict)
    # which events fire it
    on_added: bool = True
    on_failed: bool = True
    on_pending_confirmation: bool = False


class ConnectSettings(BaseModel):
    targets: list[ConnectTarget] = Field(default_factory=list)


class GeneralSettings(BaseModel):
    url_base: str = ""
    api_key: str = ""  # Reelarr's own API key (plain-text field + Reset, spec §1)
    auth_method: str = "forms"  # "forms" | "disabled_for_local"
    log_level: str = "info"


class UiSettings(BaseModel):
    theme: str = "dark"  # dark | light | auto
    color_impaired_mode: bool = False


SECTION_SCHEMAS: dict[str, type[BaseModel]] = {
    "fulfillment": FulfillmentSettings,
    "identification": IdentificationSettings,
    "metadata": MetadataSettings,
    "connect": ConnectSettings,
    "general": GeneralSettings,
    "ui": UiSettings,
}

T = TypeVar("T", bound=BaseModel)


def _env_defaults(section: str) -> dict:
    cfg = get_config()
    if section == "fulfillment":
        return FulfillmentSettings(
            target=cfg.fulfillment_target,
            radarr_url=cfg.radarr_url,
            radarr_api_key=cfg.radarr_api_key,
            radarr_root_folder=cfg.radarr_root_folder,
            radarr_quality_profile_id=cfg.radarr_quality_profile_id,
            sonarr_url=cfg.sonarr_url,
            sonarr_api_key=cfg.sonarr_api_key,
            sonarr_root_folder=cfg.sonarr_root_folder,
            sonarr_quality_profile_id=cfg.sonarr_quality_profile_id,
            seerr_url=cfg.seerr_url,
            seerr_api_key=cfg.seerr_api_key,
        ).model_dump()
    if section == "identification":
        return IdentificationSettings(
            litellm_base_url=cfg.litellm_base_url,
            litellm_api_key=cfg.litellm_api_key,
            text_model=cfg.text_model,
            vision_model=cfg.vision_model,
            stt_model=cfg.stt_model,
            stt_base_url=cfg.stt_base_url,
            stt_api_key=cfg.stt_api_key,
            enable_vision=cfg.enable_vision,
            frame_count=cfg.frame_count,
            max_video_minutes=cfg.max_video_minutes,
            max_video_height=cfg.max_video_height,
        ).model_dump()
    if section == "metadata":
        return MetadataSettings(tmdb_api_key=cfg.tmdb_api_key).model_dump()
    if section == "general":
        return GeneralSettings(url_base=cfg.url_base, log_level=cfg.log_level).model_dump()
    return SECTION_SCHEMAS[section]().model_dump()


def get_section(db: Session, section: str, schema: type[T]) -> T:
    defaults = _env_defaults(section)
    row = db.get(SettingsSection, section)
    if row is not None:
        defaults.update(row.payload or {})
    return schema.model_validate(defaults)


def save_section(db: Session, section: str, payload: dict) -> BaseModel:
    schema = SECTION_SCHEMAS[section]
    validated = schema.model_validate(payload)
    row = db.get(SettingsSection, section)
    if row is None:
        row = SettingsSection(section=section, payload=validated.model_dump())
        db.add(row)
    else:
        row.payload = validated.model_dump()
    db.commit()
    return validated
