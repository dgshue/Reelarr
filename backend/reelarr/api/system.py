"""System → Status / Tasks / Backup / Updates / Events / Log Files.

Status + Events are real; Tasks/Backup/Updates/Logs are structural stubs
matching the Servarr sub-page layout (spec §1).
"""

from __future__ import annotations

import platform
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

import reelarr
from reelarr.db import get_session
from reelarr.models.requests import RequestEvent

router = APIRouter(prefix="/system", tags=["system"])

_started_at = datetime.now(timezone.utc)


@router.get("/status")
def status() -> dict:
    return {
        "app_name": "Reelarr",
        "version": reelarr.__version__,
        "python_version": platform.python_version(),
        "started_at": _started_at.isoformat(),
        # Health issues surface here and as the sidebar badge count — no
        # toasts (spec §1). TODO(health): populate from real checks
        # (LiteLLM reachable, model pulled, fulfillment target reachable,
        # newer image available).
        "health": [],
    }


@router.get("/events")
def events(db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(
        select(RequestEvent).order_by(RequestEvent.created_at.desc()).limit(200)
    ).scalars().all()
    return [
        {
            "id": e.id,
            "request_id": e.request_id,
            "event_type": e.event_type,
            "detail": e.detail,
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]


@router.get("/tasks")
def tasks() -> list[dict]:
    # TODO(tasks): scheduled jobs (backups, blocklist cleanup, image update check).
    return []


@router.get("/backup")
def backups() -> list[dict]:
    # TODO(backup): manual "Backup Now" (kept forever) + scheduled backups
    # (folder/interval/retention), restore via upload — spec §1.
    return []


@router.get("/updates")
def updates() -> dict:
    # Docker-first: no self-updater; health check just flags "newer image
    # available" (spec §1). TODO(updates): compare running image digest
    # against dgshue/reelarr:latest on Docker Hub.
    return {"current_version": reelarr.__version__, "update_available": False}


@router.get("/logs")
def log_files() -> list[dict]:
    # TODO(logging): rotating file logs capped and counted like Radarr's.
    return []
