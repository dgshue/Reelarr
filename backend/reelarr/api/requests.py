"""Library / Activity (Queue, History, Blocklist) / Pending Confirmation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from reelarr.db import get_session
from reelarr.models.requests import BlocklistEntry, MediaRequest, RequestStatus

router = APIRouter(tags=["requests"])

IN_FLIGHT = (RequestStatus.QUEUED, RequestStatus.IDENTIFYING, RequestStatus.FULFILLING)
TERMINAL = (
    RequestStatus.FULFILLED,
    RequestStatus.ALREADY_EXISTS,
    RequestStatus.FAILED,
    RequestStatus.DISMISSED,
)


def _serialize(r: MediaRequest) -> dict:
    return {
        "id": r.id,
        "url": r.url,
        "platform": r.platform,
        "source_channel": r.source_channel,
        "status": r.status.value,
        "resolved_tier": r.resolved_tier,
        "confidence": r.confidence,
        "title": r.title,
        "year": r.year,
        "media_type": r.media_type,
        "tmdb_id": r.tmdb_id,
        "tvdb_id": r.tvdb_id,
        "poster_url": r.poster_url,
        "overview": r.overview,
        "candidates": r.candidates,
        "fulfillment_target": r.fulfillment_target,
        "error": r.error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("/library")
def library(db: Session = Depends(get_session)) -> list[dict]:
    """Identified + fulfilled clips (poster grid)."""
    rows = db.execute(
        select(MediaRequest)
        .where(MediaRequest.status.in_((RequestStatus.FULFILLED, RequestStatus.ALREADY_EXISTS)))
        .order_by(MediaRequest.updated_at.desc())
    ).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/activity/queue")
def activity_queue(db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(
        select(MediaRequest)
        .where(MediaRequest.status.in_(IN_FLIGHT))
        .order_by(MediaRequest.created_at.desc())
    ).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/activity/history")
def activity_history(db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(
        select(MediaRequest)
        .where(MediaRequest.status.in_(TERMINAL))
        .order_by(MediaRequest.updated_at.desc())
        .limit(200)
    ).scalars().all()
    return [_serialize(r) for r in rows]


@router.get("/activity/blocklist")
def activity_blocklist(db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(
        select(BlocklistEntry).order_by(BlocklistEntry.created_at.desc())
    ).scalars().all()
    return [
        {"id": b.id, "url": b.url, "reason": b.reason, "created_at": b.created_at.isoformat()}
        for b in rows
    ]


@router.delete("/activity/blocklist/{entry_id}")
def remove_blocklist_entry(entry_id: int, db: Session = Depends(get_session)) -> dict:
    entry = db.get(BlocklistEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "Not found")
    db.delete(entry)
    db.commit()
    return {"ok": True}


@router.get("/pending")
def pending_confirmation(db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(
        select(MediaRequest)
        .where(MediaRequest.status == RequestStatus.PENDING_CONFIRMATION)
        .order_by(MediaRequest.created_at.desc())
    ).scalars().all()
    return [_serialize(r) for r in rows]


# NOTE: confirming from the web UI (in addition to the originating channel) is
# supported by design — it goes through the same processor path.
# TODO(app-wiring): route these through the running RequestProcessor instance
# once the intake service is started with real credentials; for now they 501.


@router.post("/pending/{request_id}/confirm/{index}")
async def confirm_from_ui(request_id: int, index: int) -> dict:
    raise HTTPException(501, "UI-side confirmation wiring pends RequestProcessor startup")


@router.post("/pending/{request_id}/dismiss")
async def dismiss_from_ui(request_id: int) -> dict:
    raise HTTPException(501, "UI-side confirmation wiring pends RequestProcessor startup")
