"""Reelarr app factory / entrypoint.

Run locally:  uvicorn reelarr.main:app --port 7979 --reload
In Docker:    CMD in the image runs the same thing.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from reelarr.ai.openai_compat import (
    OpenAICompatSttClient,
    OpenAICompatTextClient,
    OpenAICompatVisionClient,
)
from reelarr.api import api_router
from reelarr.config import get_config
from reelarr.db import get_engine, init_db
from reelarr.intake.base import IntakeChannel
from reelarr.intake.telegram import TelegramChannel
from reelarr.intake.whatsapp import WhatsAppChannel
from reelarr.fulfillment.arr import DirectFulfillment, RadarrClient, SonarrClient
from reelarr.fulfillment.seerr import SeerrClient
from reelarr.pipeline.identify import IdentificationPipeline
from reelarr.pipeline.media import YtDlpResolver
from reelarr.pipeline.tmdb import TmdbClient
from reelarr.services.processor import RequestProcessor

logger = logging.getLogger("reelarr")


def _configure_logging(level: str) -> None:
    """Attach the app's logger to stdout at the configured level.

    Without this, uvicorn only configures its *own* loggers, so everything
    Reelarr logs — including swallowed intake-channel startup failures — goes
    nowhere, which makes "the bot silently isn't running" undiagnosable.
    """
    reelarr_logger = logging.getLogger("reelarr")
    if reelarr_logger.handlers:  # already configured (e.g. reload)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    reelarr_logger.addHandler(handler)
    reelarr_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    reelarr_logger.propagate = False


def _build_processor(cfg) -> tuple[RequestProcessor | None, dict[str, IntakeChannel]]:
    """Wire pipeline + fulfillment + channels from current config.

    Channels without credentials are skipped (they can be configured later in
    Settings -> Sources; TODO(intake): hot-reload channels on settings save
    instead of requiring a restart).
    """
    from sqlalchemy.orm import sessionmaker

    channels: dict[str, IntakeChannel] = {}
    if cfg.telegram_bot_token:
        channels["telegram"] = TelegramChannel(
            cfg.telegram_bot_token,
            {c.strip() for c in cfg.telegram_allowed_chat_ids.split(",") if c.strip()},
        )
    if cfg.evolution_api_url:
        channels["whatsapp"] = WhatsAppChannel(
            cfg.evolution_api_url,
            cfg.evolution_api_key,
            cfg.evolution_instance,
            {n.strip() for n in cfg.whatsapp_allowed_numbers.split(",") if n.strip()},
        )
    # TODO(discord)/TODO(slack): construct once those adapters are wired
    # (they currently raise NotImplementedError from start()).

    if not channels:
        return None, {}

    resolver = YtDlpResolver(
        cfg.tmp_dir, cfg.cookies_dir, cfg.max_video_height, cfg.max_video_minutes,
        frame_width=cfg.frame_width,
    )
    text_llm = OpenAICompatTextClient(cfg.litellm_base_url, cfg.text_model, cfg.litellm_api_key)
    vision_llm = OpenAICompatVisionClient(
        cfg.litellm_base_url, cfg.vision_model, cfg.litellm_api_key
    )
    stt = OpenAICompatSttClient(
        cfg.stt_base_url or cfg.litellm_base_url,
        cfg.stt_model,
        cfg.stt_api_key or cfg.litellm_api_key,
    )
    tmdb = TmdbClient(cfg.tmdb_api_key)
    pipeline = IdentificationPipeline(
        resolver=resolver,
        text_llm=text_llm,
        stt=stt,
        tmdb=tmdb,
        vision_llm=vision_llm,
        enable_vision=cfg.enable_vision,
        frame_count=cfg.frame_count,
    )

    if cfg.fulfillment_target == "seerr":
        fulfillment = SeerrClient(cfg.seerr_url, cfg.seerr_api_key)
    else:
        fulfillment = DirectFulfillment(
            RadarrClient(
                cfg.radarr_url, cfg.radarr_api_key,
                cfg.radarr_root_folder, cfg.radarr_quality_profile_id,
            ),
            SonarrClient(
                cfg.sonarr_url, cfg.sonarr_api_key,
                cfg.sonarr_root_folder, cfg.sonarr_quality_profile_id,
            ),
        )

    session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    processor = RequestProcessor(
        db_factory=session_factory,
        pipeline=pipeline,
        fulfillment=fulfillment,
        channels=channels,
        fulfillment_target=cfg.fulfillment_target,
    )
    for channel in channels.values():
        channel.on_link(processor.handle_link)
        channel.on_confirmation(processor.handle_confirmation)
    return processor, channels


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    _configure_logging(cfg.log_level)
    init_db()
    processor, channels = _build_processor(cfg)
    app.state.processor = processor
    app.state.channels = channels
    app.state.whatsapp_channel = channels.get("whatsapp")
    if not channels:
        logger.warning(
            "no intake channels configured — set TELEGRAM_BOT_TOKEN (or another "
            "Source) or Reelarr has no way to receive links"
        )
    for name, channel in channels.items():
        try:
            await channel.start()
            logger.info("started intake channel: %s", name)
        except NotImplementedError as exc:
            logger.warning("intake channel %s not started: %s", name, exc)
        except Exception:
            logger.exception("intake channel %s failed to start", name)
    yield
    for channel in channels.values():
        try:
            await channel.stop()
        except Exception:
            logger.exception("error stopping channel")


def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(title="Reelarr", version="0.1.0", lifespan=lifespan, root_path=cfg.url_base)
    app.include_router(api_router)

    # --- WebSocket: live queue/history updates (spec §3) --------------------
    # Minimal hub for now; TODO(realtime): broadcast request status changes
    # from RequestProcessor so the UI updates without polling. Document the
    # WebSocket upgrade-header gotcha for nginx/Caddy in README.
    ws_clients: set[WebSocket] = set()

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        ws_clients.add(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive; server pushes only
        except WebSocketDisconnect:
            ws_clients.discard(ws)

    # --- Serve the built frontend (frontend/dist) in the Docker image -------
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    # Resolve the built UI across both layouts: a source checkout (where the
    # package sits at <repo>/backend/reelarr) and the Docker image (where the
    # package is pip-installed into site-packages, far from /app/frontend/dist).
    # The old repo-relative-only lookup silently found nothing in the image and
    # served 404s for the entire UI.
    candidates = []
    if cfg.frontend_dist:
        candidates.append(Path(cfg.frontend_dist))
    candidates += [
        Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",  # source checkout
        Path("/app/frontend/dist"),                                          # docker image
    ]
    dist = next((p for p in candidates if p.is_dir()), None)
    if dist is None:
        logger.warning(
            "frontend build not found (looked in: %s) — API works but the web UI "
            "will 404; set FRONTEND_DIST to override",
            ", ".join(str(p) for p in candidates),
        )
    if dist is not None:
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        # SPA fallback: any non-API, non-asset path serves index.html so
        # client-side routes (/library, /settings/...) survive a hard refresh.
        from fastapi.responses import FileResponse

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            # Never swallow unmatched API paths — returning index.html for a
            # typo'd/removed endpoint makes clients parse HTML as JSON and get a
            # baffling error instead of a clean 404.
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found")
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
