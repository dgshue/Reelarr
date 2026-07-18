"""Settings routers — one sub-path per Settings nav page, each with the
Test-button contract from spec §2 (validate + live-populate in one call)."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from reelarr.ai.openai_compat import OpenAICompatTextClient
from reelarr.db import get_session
from reelarr.fulfillment.arr import RadarrClient, SonarrClient
from reelarr.fulfillment.seerr import SeerrClient
from reelarr.intake.discord import DiscordChannel
from reelarr.intake.slack import SlackChannel
from reelarr.intake.telegram import TelegramChannel
from reelarr.intake.whatsapp import WhatsAppChannel
from reelarr.models.sources import SourceAllowlistEntry, SourceChannel
from reelarr.pipeline.tmdb import TmdbClient
from reelarr.services import connect as connect_service
from reelarr.services import settings as settings_service
from reelarr.services.settings import (
    SECTION_SCHEMAS,
    ConnectTarget,
    FulfillmentSettings,
    IdentificationSettings,
    MetadataSettings,
)

router = APIRouter(prefix="/settings", tags=["settings"])


# --- Generic section get/save -------------------------------------------------


@router.get("/{section}")
def get_section(section: str, db: Session = Depends(get_session)) -> dict:
    if section not in SECTION_SCHEMAS:
        raise HTTPException(404, f"Unknown settings section: {section}")
    return settings_service.get_section(db, section, SECTION_SCHEMAS[section]).model_dump()


@router.put("/{section}")
def save_section(section: str, payload: dict = Body(...), db: Session = Depends(get_session)) -> dict:
    if section not in SECTION_SCHEMAS:
        raise HTTPException(404, f"Unknown settings section: {section}")
    return settings_service.save_section(db, section, payload).model_dump()


# --- Fulfillment tests (real HTTP calls) --------------------------------------


@router.post("/fulfillment/test/radarr")
async def test_radarr(payload: FulfillmentSettings) -> dict:
    client = RadarrClient(payload.radarr_url, payload.radarr_api_key)
    try:
        return await client.test()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/fulfillment/test/sonarr")
async def test_sonarr(payload: FulfillmentSettings) -> dict:
    client = SonarrClient(payload.sonarr_url, payload.sonarr_api_key)
    try:
        return await client.test()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/fulfillment/test/seerr")
async def test_seerr(payload: FulfillmentSettings) -> dict:
    client = SeerrClient(payload.seerr_url, payload.seerr_api_key)
    try:
        return await client.test()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}


# --- Identification (AI backend) test ------------------------------------------


@router.post("/identification/test")
async def test_identification(payload: IdentificationSettings) -> dict:
    """LiteLLM connectivity + configured-model presence (spec §2).

    Distinguishes 'can't reach the proxy at all' from 'proxy is up but the
    configured model isn't in its model list' (the classic never-`ollama
    pull`ed failure mode), and returns the model list so the UI can offer a
    dropdown instead of free text."""
    client = OpenAICompatTextClient(
        payload.litellm_base_url, payload.text_model, payload.litellm_api_key
    )
    try:
        models = await client.list_models()
    except httpx.HTTPError as exc:
        return {"ok": False, "reachable": False, "error": f"Cannot reach LiteLLM: {exc}"}
    finally:
        await client.aclose()
    missing = [
        m
        for m in (payload.text_model, payload.vision_model, payload.stt_model)
        if m and m not in models
    ]
    return {
        "ok": not missing,
        "reachable": True,
        "models": models,
        "missing_models": missing,
        "error": (
            f"Configured model(s) not available on the proxy: {', '.join(missing)}. "
            "If they map to Ollama models, check they've been `ollama pull`ed."
        )
        if missing
        else None,
    }


# --- Metadata (TMDB) test -------------------------------------------------------


@router.post("/metadata/test")
async def test_tmdb(payload: MetadataSettings) -> dict:
    client = TmdbClient(payload.tmdb_api_key)
    try:
        await client.test()
        return {"ok": True}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return {"ok": False, "error": "TMDB rejected the API key"}
        return {"ok": False, "error": str(exc)}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        await client.aclose()


# --- Sources (intake channels) ---------------------------------------------------


@router.get("/sources/channels")
def list_source_channels(db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(select(SourceChannel)).scalars().all()
    return [
        {
            "id": r.id,
            "channel_type": r.channel_type,
            "enabled": r.enabled,
            "config": r.config,
            "allowlist": [
                {"id": e.id, "ref_type": e.ref_type, "ref_value": e.ref_value, "label": e.label}
                for e in r.allowlist
            ],
        }
        for r in rows
    ]


@router.put("/sources/channels/{channel_type}")
def save_source_channel(
    channel_type: str, payload: dict = Body(...), db: Session = Depends(get_session)
) -> dict:
    if channel_type not in ("telegram", "discord", "slack", "whatsapp"):
        raise HTTPException(404, f"Unknown channel type: {channel_type}")
    row = db.execute(
        select(SourceChannel).where(SourceChannel.channel_type == channel_type)
    ).scalar_one_or_none()
    if row is None:
        row = SourceChannel(channel_type=channel_type)
        db.add(row)
    row.enabled = bool(payload.get("enabled", row.enabled))
    row.config = payload.get("config", row.config) or {}
    if "allowlist" in payload:
        row.allowlist.clear()
        for entry in payload["allowlist"]:
            row.allowlist.append(
                SourceAllowlistEntry(
                    ref_type=entry.get("ref_type", "chat_id"),
                    ref_value=str(entry["ref_value"]),
                    label=entry.get("label"),
                )
            )
    db.commit()
    return {"ok": True, "channel_type": channel_type, "enabled": row.enabled}
    # NOTE: a restart (or channel manager reload) applies the change to the
    # running bots — TODO(intake): hot-reload channels on save.


@router.post("/sources/{channel_type}/test")
async def test_source(channel_type: str, payload: dict = Body(...)) -> dict:
    """Per-channel Test per the spec §2 table. Structurally real for all four;
    Discord/Slack/WhatsApp will fail until real credentials exist."""
    config = payload.get("config", {})
    try:
        if channel_type == "telegram":
            return await TelegramChannel(config.get("bot_token", "")).test()
        if channel_type == "discord":
            return await DiscordChannel(config.get("bot_token", "")).test()
        if channel_type == "slack":
            return await SlackChannel(
                config.get("bot_token", ""), config.get("app_token", "")
            ).test()
        if channel_type == "whatsapp":
            return await WhatsAppChannel(
                config.get("base_url", ""),
                config.get("api_key", ""),
                config.get("instance", "reelarr"),
            ).test()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)}
    raise HTTPException(404, f"Unknown channel type: {channel_type}")


# --- Connect (outbound notifications) -----------------------------------------------


@router.post("/connect/test")
async def test_connect_target(target: ConnectTarget) -> dict:
    """Test = send a real test notification (spec §2)."""
    return await connect_service.test_target(target)
